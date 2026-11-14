
import os
import uuid
import requests


API_BASE = "https://api.sumup.com/v0.1"


class SumUpError(Exception):
    """Raised when a SumUp API request fails."""
    pass


def get_credentials():
    api_key = os.getenv("SUMUP_API_KEY")
    merchant_code = os.getenv("SUMUP_MERCHANT_CODE")

    if not api_key or not merchant_code:
        raise SumUpError(
            "Missing SUMUP_API_KEY or SUMUP_MERCHANT_CODE"
        )

    return api_key, merchant_code


def api_request(method, endpoint, payload=None):

    api_key, _ = get_credentials()

    try:
        response = requests.request(
            method,
            f"{API_BASE}{endpoint}",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=30,
        )

    except requests.RequestException as err:
        raise SumUpError(f"Unable to contact SumUp: {err}") from err

    try:
        result = response.json()
    except ValueError:
        result = {}

    if not response.ok:
        raise SumUpError(
            f"SumUp API error ({response.status_code}): {result or response.text}"
        )

    return result


def create_checkout(
    amount,
    description,
    checkout_reference=None,
    currency="GBP",
    return_url=None,
):

    _, merchant_code = get_credentials()

    if checkout_reference is None:
        checkout_reference = f"test-{uuid.uuid4()}"

    payload = {
        "checkout_reference": checkout_reference,
        "amount": amount,
        "currency": currency,
        "merchant_code": merchant_code,
        "description": description,
        "hosted_checkout": {
            "enabled": True
        },
    }
    
    if return_url:
      payload["return_url"] = return_url
      
    if redirect_url:
    payload["redirect_url"] = redirect_url

    return api_request(
        "POST",
        "/checkouts",
        payload,
    )


def get_checkout(checkout_id):

    return api_request(
        "GET",
        f"/checkouts/{checkout_id}",
    )

def get_webhook_url():

    token = os.getenv("SUMUP_WEBHOOK_TOKEN")

    if not token:
        raise SumUpError("Missing SUMUP_WEBHOOK_TOKEN")

    return (
        "https://wy4fap3sc9.execute-api.eu-north-1.amazonaws.com"
        f"/sumup/webhook/{token}"
    )