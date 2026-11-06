
import json


def lambda_handler(event, context):

    response = {
        "message": "Registration API is running",
        "status": "ok"
    }

    return {
        "statusCode": 200,
        "headers": {
            "Content-Type": "application/json"
        },
        "body": json.dumps(response)
    }
