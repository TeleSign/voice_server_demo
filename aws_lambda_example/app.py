from datetime import datetime
import json
import logging
import random
import traceback

import boto3
from chalice import Chalice, Response
from chalicelib import config, telesign

app = Chalice(app_name='vox_aws_chalice_example')
app.debug = True

logger = logging.getLogger()
logger.setLevel(logging.DEBUG)

sqs = boto3.client('sqs')
dynamodb = boto3.client('dynamodb')


@app.route('/health', methods=['GET'])
def health():
    lambda_context = app.lambda_context
    data = {
        'remaining_time_in_millis': lambda_context.get_remaining_time_in_millis(),
        'log_stream_name': lambda_context.log_stream_name,
        'log_group_name': lambda_context.log_group_name,
        'aws_request_id': lambda_context.aws_request_id,
        'memory_limit_in_mb': lambda_context.memory_limit_in_mb,
        'utc': datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
    }
    return Response(data)


@app.route('/set_reminder', methods=['POST'], content_types=['application/json'])
def set_reminder():
    request = app.current_request
    data = {
        'to_phone_number': request.json_body['to_phone_number'],
        'message': request.json_body['message'],
        'delay_seconds': request.json_body['delay_seconds'],
        'enable_snooze': request.json_body['enable_snooze'],
    }

    reminder_queue_url = sqs.get_queue_url(QueueName=config.reminder_queue_name)
    response = sqs.send_message(QueueUrl=reminder_queue_url['QueueUrl'],
                                MessageBody=json.dumps(data),
                                DelaySeconds=data['delay_seconds'])
    logger.debug(('sqs.send_message', response))

    return Response("OK")


@app.on_sqs_message(queue=config.reminder_queue_name, batch_size=1)
def handle_reminder_queue(event):
    for record in event:
        try:
            logger.debug(("handle_reminder_queue: ", record.body))
            data = json.loads(record.body)
            random.shuffle(config.caller_ids)
            caller_id = config.caller_ids[0]
            dial_response = telesign.dial(data['to_phone_number'], caller_id)
            logger.debug(('telesign_dial_response', dial_response.__dict__))
            dial_response_json = dial_response.json()
            reference_id = dial_response_json['reference_id']

            dynamodb.put_item(TableName=config.call_flow_dynamodb_table,
                              Item={
                                  'reference_id': {'S': reference_id},
                                  'type': {'S': "reminder"},
                                  'data': {'S': json.dumps(data)},
                                  'app_log': {
                                      'L': [{
                                          'M': {
                                              'direction': {'S': 'outbound'},
                                              'action': {'S': 'dial'},
                                              'response_json': {'S': json.dumps(dial_response_json)}
                                          }
                                      }]
                                  },
                              })

        except Exception as ex:
            logger.error(traceback.format_exc())


def handle_reminder_dial_completed(request_data, call_flow_data):
    if request_data['data']['status'] == 'answered':
        data = json.loads(call_flow_data['Item']['data']['S'])
        if data['enable_snooze']:
            digits_to_collect = 1
            message = f"{data['message']}, press 1 to snooze"
        else:
            digits_to_collect = 0
            message = data['message']

        response = telesign.generate_speak_response(message, digits_to_collect=digits_to_collect)
    else:
        response = telesign.generate_hangup_response()

    app_log_entry = {
        'M': {
            'direction': {'S': 'inbound'},
            'action': {'S': 'dial_completed'},
            'response_json': {'S': json.dumps(response)}
        }
    }
    call_flow_data['Item']['app_log']['L'].append(app_log_entry)

    dynamodb.put_item(TableName=config.call_flow_dynamodb_table,
                      Item=call_flow_data['Item'])

    return response


def handle_reminder_speak_completed(request_data, call_flow_data):
    logger.debug(('handle_reminder_speak_completed', request_data, call_flow_data))

    if request_data['data']['status'] == 'speak_successful':
        data = json.loads(call_flow_data['Item']['data']['S'])
        if data['enable_snooze'] and request_data['data']['collected_digits'] == '1':
            sqs = boto3.client('sqs')
            reminder_queue_url = sqs.get_queue_url(QueueName=config.reminder_queue_name)
            sqs_response = sqs.send_message(QueueUrl=reminder_queue_url['QueueUrl'],
                                            MessageBody=json.dumps(data),
                                            DelaySeconds=data['delay_seconds'])
            logger.debug(('snooze sqs.send_message', sqs_response))

            app_log_entry = {
                'M': {
                    'action': {'S': 'snooze'},
                }
            }
            call_flow_data['Item']['app_log']['L'].append(app_log_entry)

    response = telesign.generate_hangup_response()

    app_log_entry = {
        'M': {
            'direction': {'S': 'inbound'},
            'action': {'S': 'speak_completed'},
            'response_json': {'S': json.dumps(response)}
        }
    }
    call_flow_data['Item']['app_log']['L'].append(app_log_entry)

    dynamodb.put_item(TableName=config.call_flow_dynamodb_table,
                      Item=call_flow_data['Item'])

    return response


def handle_call_completed(request_data, call_flow_data):

    app_log_entry = {
        'M': {
            'direction': {'S': 'inbound'},
            'action': {'S': 'call_completed'}
        }
    }
    call_flow_data['Item']['app_log']['L'].append(app_log_entry)

    dynamodb.put_item(TableName=config.call_flow_dynamodb_table,
                      Item=call_flow_data['Item'])
    return {}


@app.route('/telesign_vox_callback', methods=['POST'], content_types=['application/json'])
def telesign_vox_callback():
    request = app.current_request
    request_data = json.loads(request.raw_body.decode())
    logger.debug(('telesign_vox_callback', request_data))

    reference_id = request_data['reference_id']
    event = request_data['event']
    client = boto3.client('dynamodb')
    call_flow_data = client.get_item(TableName=config.call_flow_dynamodb_table,
                                     Key={'reference_id': {'S': reference_id}})
    logger.debug(('telesign_vox_callback_call_flow', call_flow_data.get('Item')))

    type = None
    if call_flow_data.get('Item'):
        type = call_flow_data['Item']['type']['S']

    response = telesign.generate_hangup_response()

    if type == 'reminder':
        if event == 'dial_completed':
            response = handle_reminder_dial_completed(request_data, call_flow_data)

        elif event == 'speak_completed':
            response = handle_reminder_speak_completed(request_data, call_flow_data)

    if event == 'call_completed':
        response = handle_call_completed(request_data, call_flow_data)

    return Response([response])
