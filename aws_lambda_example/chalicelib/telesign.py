import base64
import requests

from .config import telesign_customer_id, telesign_api_key, caller_ids


api_endpoint = "https://rest.telesign.com/v2/voice"


def get_authorization_header():
    authorization_type = "Basic"
    credentials = base64.b64encode(f"{telesign_customer_id}:{telesign_api_key}".encode()).decode()
    return f"{authorization_type} {credentials}"


def dial(to_phone_number, caller_id_number):

    payload = {
        'method': "dial",
        'params': {'to': to_phone_number, 'caller_id_number': caller_id_number},
        'id': "1",
        'jsonrpc': "2.0",
    }
    headers = {
        'Accept': "application/json",
        'Content-Type': "application/json",
        'Authorization': get_authorization_header()
    }
    response = requests.post(api_endpoint, json=payload, headers=headers)

    return response


def generate_speak_response(message, language='en-US', digits_to_collect=0):
    payload = {
        'jsonrpc': "2.0",
        'method': 'speak',
        'params': {
            'tts': {
                'message': message,
                'language': language,
            }
        },
    }
    if digits_to_collect and digits_to_collect > 0:
        payload['params']['collect_digits'] = {'max': digits_to_collect}

    return payload


def generate_hangup_response():
    payload = {
        'jsonrpc': "2.0",
        'method': 'hangup',
        'params': {},
    }
    return payload
