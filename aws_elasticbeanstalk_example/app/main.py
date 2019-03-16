from datetime import datetime
from typing import Optional
from json import dumps

import bottle
from bottle import run, request, Bottle

app = Bottle()
headers = {
    'Content-Type': "application/json",
}

# This is a an example response to an Incoming Call event
# {
#     "jsonrpc": "2.0",
#     "method": "dial",
#     "params": {
#         "caller_id_number": "18888888888", # this is your assigned virtual number
#         "to": "19999999999"  # this is the intended destination number
#     }
# }

class TelesignEvent:
    """
    Events Telesign Voice API sends.
    """
    INCOMING_CALL = "incoming_call"
    CALL_COMPLETED = "call_completed"
    CALL_LEG_COMPLETED = "call_leg_completed"
    ANSWERED = "dial_completed"
    PLAY_COMPLETED = "play_completed"
    SPEAK_COMPELTED = "speak_completed"

class DialAction:
    def __init__(self, to: str, caller_id_number: str):
        self.method: str = 'dial'
        self.parameters = {
            'to': to,
            'caller_id_number': caller_id_number,
        }

class SpeakAction:
    def __init__(self, tts_message: str, language: Optional[str]=None, collect_digits: bool=False, digits_to_collect: int=1):
        self.method: str = 'speak'
        self.parameters: dict = {
            'tts': {
                'message': tts_message,
                'language': language,
            }
        }
        if collect_digits:
            self.parameters['collect_digits'] = {
                'max': digits_to_collect,
            }
    
class HangupAction:
    def __init__(self):
        self.method = 'hangup'
        self.parameters = {}
    
def generate_response(action):
    return dumps({
        'jsonrpc': '2.0',
        'method': action.method,
        'params': action.parameters
    })


@app.get('/health')
def health():
    return bottle.HTTPResponse({
        'service': 'python_customer_server',
        'description': 'endpoints for customer server',
        'pinged_on': datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
    })


@app.post('/')
def telesign_event():
    next_actions = inbound_ivr_flow(request)
    payload = generate_response(next_actions)
    return bottle.HTTPResponse(body=payload, headers=headers)


##################################################################
########      Example workflows for inspiration      #############
##################################################################

def inbound_call_transfer_flow(request):
    """
    Implements a 3 leg flow respectively called the A, B, and C legs.
    Leg A is an inbound call into the virtual number from the A user.
    Leg B is an outbound call that bridges the A and B legs together.
    Leg C is an outbound call, that is triggered when B hangs up, that
    bridges the A and C legs together.

    The following scenario represents a call-center for customer support.
    Th
    """
    
    event = request.json.get('event')
    to_number = request.json.get('to')
    from_number = request.json.get('from')

    # replace the following with your phone numbers
    first_tier_support_number = '1'
    second_tier_support_number = '1'
    manager_number = '1'
    virtual_number = '1'
    
    if event == TelesignEvent.INCOMING_CALL and from_number == first_tier_support_number:
        return DialAction(
            to=second_tier_support_number,
            caller_id_number=virtual_number)
        
    elif event == TelesignEvent.CALL_LEG_COMPLETED and to_number == second_tier_support_number:
        return DialAction(
            to=manager_number,
            caller_id_number=virtual_number)
    
    elif event == TelesignEvent.CALL_COMPLETED:
        #Call completed event can be responded to but it is ignored because the
        #call has already be terminated.
        record_cdr(request.json)
        
    else:
        return HangupAction()


def outbound_call_survey_flow(request):
    """
    The following scenario describes an outbound call you initiated to an end-user with a survey at the end.

    A customer dialed your customer support number.
    You present an option to either wait in a queue or you will dial them later. They choose to be called later.
    Your agent queue system fires an outbound call to the customer using the Telesign API.
    Once the end user picks up we connect them to an agent.
    Afterwards you would like to know how satisfied they were with their service, so you send a survey.
    """
    event = request.json.get('event')
    sender_id = request.json['data']['from']

    # replace the following with your phone numbers
    CUSTOMER_SERVICE_AGENT = '1111111'

    call_session_id =  request.json.get('reference_id')

    if event == TelesignEvent.ANSWERED:
        return DialAction(to=CUSTOMER_SERVICE_AGENT,
                          caller_id_number=sender_id)

    elif event == TelesignEvent.CALL_LEG_COMPLETED:
        return SpeakAction('How would you rate your service today? Select a digit between 1 and 5',
                           collect_digits=True,
                           digits_to_collect=1)

    elif event == TelesignEvent.SPEAK_COMPELTED:
        survey_response = request.json['data']['collected_digits']
        record_survey_response(survey_response)

        return HangupAction()
    
    elif event == TelesignEvent.CALL_COMPLETED:
        #Call completed event can be responded to but it is ignored because the
        #call has already be terminated.
        record_cdr(request.json)

    else:
        return HangupAction()

        
def inbound_ivr_flow(request):
    """
    This scenario demonstrates a customer calling into your call center.
    Through some fancy account lookup you know their name is Dave, so we can use it throughout the call.
    Dave will comb through the menu and we connect him with the appropriate department.
    """

    event = request.json.get('event')

    # replace the following with your phone numbers
    virtual_number = '1111111'
    customer_service_number = '3333333'
    finance_dept_number = '2222'

    selected_digit_to_department_mapping = {
        '1': customer_service_number,
        '2': finance_dept_number,
    }

    if event == TelesignEvent.INCOMING_CALL:
        # Generate the action in the JSON format used by TeleSign. Refer to the Voice documentation for details.
        ivr_message = 'Hello Dave, how can we help you today? Press 1 for Customer Service. Press 2 for account department.'
        return SpeakAction(
            tts_message=ivr_message,
            language='en-US',
            collect_digits=True
        )

    elif event == TelesignEvent.SPEAK_COMPELTED:
        selected_department = request.json['data']['collected_digits']

        if selected_department in selected_digit_to_department_mapping:
            return DialAction(
                caller_id_number=virtual_number,
                to=selected_digit_to_department_mapping[selected_department],
            )
        else:
            return HangupAction()
    
    elif event == TelesignEvent.CALL_COMPLETED:
        # Telesign does not process your response, so responding is unnecessary
        record_cdr(request.json)

    else:
        # You do not know the number
        return HangupAction()


def record_cdr(call_completed_event):
    """Store this transaction log somewhere

    Below is an example of the JSON you'll receive (check docs for up to date data structure)

    {
      "reference_id": "C58C8EB012D",
      "event": "call_completed",
      "data": {
        "duration": 6,
        "created_on_utc": "2019-03-15T06:48:22.151",
        "answered_on_utc": "2019-03-15T06:48:24.151",
        "ended_on_utc": "2019-03-15T06:48:28.151",
        "status": "hangup",
        "audio_recording_file_name": null,
        "to": "DESTINATION_NUMBER",
        "from": "VIRTUAL_NUMBER",
        "direction": "outbound"
      }
    }
    """
    pass

def record_survey_response(survey_response):
    pass


if __name__ == '__main__':
    run(app, host='0.0.0.0', port='8080')
