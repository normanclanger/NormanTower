
import json
import yaml
from pathlib import Path


# Load the event configuration packaged alongside this Lambda.
CONFIG_PATH = Path(__file__).parent / "config" / "registration.yaml"

with open(CONFIG_PATH, "r", encoding="utf-8") as file:
    CONFIG = yaml.safe_load(file)


def lambda_handler(event, context):

    response = {
        "status": "ok",
        "event": CONFIG["event"],
        "categories": CONFIG["categories"]
    }

    return {
        "statusCode": 200,
        "headers": {
            "Content-Type": "application/json"
        },
        "body": json.dumps(response)
    }
