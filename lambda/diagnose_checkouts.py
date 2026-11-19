
from sumup_client import list_checkouts


REFERENCES = [
    "registration-18",
    "registration-19",
]


def main():

    for reference in REFERENCES:

        print()
        print("=" * 60)
        print(f"Checking SumUp reference: {reference}")
        print("=" * 60)

        try:
            checkouts = list_checkouts(reference)

            if not checkouts:
                print("No matching checkouts found.")
                continue

            for checkout in checkouts:

                print(f"Checkout ID:       {checkout.get('id')}")
                print(f"Reference:         {checkout.get('checkout_reference')}")
                print(f"Amount:            {checkout.get('amount')} "
                      f"{checkout.get('currency')}")
                print(f"Status:            {checkout.get('status')}")
                print(f"Created:           {checkout.get('date')}")
                print(f"Valid until:       {checkout.get('valid_until')}")
                print(f"Merchant code:     {checkout.get('merchant_code')}")
                print(f"Description:       {checkout.get('description')}")
                print(f"Hosted checkout:   {checkout.get('hosted_checkout_url')}")
                print()

        except Exception as error:
            print(f"ERROR: {error}")


if __name__ == "__main__":
    main()
