
document.addEventListener("DOMContentLoaded", () => {

    const form = document.getElementById("registration-form");

    if (!form) {
        return;
    }

    const API_URL =
        "https://wy4fap3sc9.execute-api.eu-north-1.amazonaws.com/api/registration/submit";

    const totalElement = document.getElementById("total");
    const resultElement = document.getElementById("registration-result");
    const resultDataElement = document.getElementById("registration-result-data");
    const submitButton = form.querySelector('button[type="submit"]');

    const quantityInputs = form.querySelectorAll(
        'input[name^="quantities["]'
    );

    const currency = new Intl.NumberFormat("en-GB", {
        style: "currency",
        currency: "GBP"
    });

    // Calculate the total in pence to avoid floating-point rounding issues.

    function calculateTotal() {

        let totalPence = 0;

        quantityInputs.forEach(input => {

            const quantity = Math.max(
                0,
                parseInt(input.value, 10) || 0
            );

            const pricePence = Math.round(
                Number(input.dataset.price) * 100
            );

            totalPence += quantity * pricePence;

        });

        return totalPence;
    }

    // Update the displayed total.

    function updateTotal() {

        const totalPence = calculateTotal();

        totalElement.textContent =
            "Total: " + currency.format(totalPence / 100);

    }

    // Recalculate whenever a quantity changes.

    quantityInputs.forEach(input => {
        input.addEventListener("input", updateTotal);
    });

    // Submit the registration to the API.

    form.addEventListener("submit", async event => {

        event.preventDefault();

        if (!form.reportValidity()) {
            return;
        }

        const items = [];

        quantityInputs.forEach(input => {

            const quantity = Math.max(
                0,
                parseInt(input.value, 10) || 0
            );

            if (quantity > 0) {

                items.push({
                    item_id: input.dataset.itemId,
                    quantity: quantity
                });

            }

        });

        const participantType = form.querySelector(
            'input[name="participant_type"]:checked'
        );

        const submission = {

            event: form.dataset.eventName,

            customer: {
                first_name: form.elements.first_name.value.trim(),
                surname: form.elements.surname.value.trim(),
                email: form.elements.email.value.trim(),
                participant_type: participantType.value,
                home_tower: form.elements.home_tower.value.trim()
            },

            items: items

        };

        // Show progress and prevent duplicate submissions.

        submitButton.disabled = true;
        resultElement.hidden = false;
        resultDataElement.textContent = "Submitting your registration...";

        resultElement.scrollIntoView({
            behavior: "smooth",
            block: "nearest"
        });

        try {

            const response = await fetch(API_URL, {
                method: "POST",
                headers: {
                    "Content-Type": "application/json"
                },
                body: JSON.stringify(submission)
            });

            const data = await response.json();

            if (!response.ok) {
                throw new Error(
                    data.error || "Unable to submit your registration."
                );
            }

            // Paid registration - redirect to SumUp.

            if (data.status === "awaiting_payment") {

                if (!data.payment?.checkout_url) {
                    throw new Error(
                        "The payment checkout URL was not returned."
                    );
                }

                resultDataElement.textContent =
                    "Registration saved. Redirecting to secure payment...";

                window.location.href = data.payment.checkout_url;

                return;
            }

            // Free registration - show confirmation.

            if (data.status === "registered") {

                if (!data.confirmation_token) {
                    throw new Error(
                        "The registration confirmation token was not returned."
                    );
                }

                window.location.href =
                    `/payment-success/?token=${encodeURIComponent(data.confirmation_token)}`;

                return;
            }

            throw new Error(
                data.message || "Unexpected response from the registration service."
            );

        } catch (error) {

            console.error("Registration submission failed:", error);

            resultDataElement.textContent =
                "Sorry, there was a problem submitting your registration.\n\n" +
                error.message;

        } finally {

            submitButton.disabled = false;

        }

    });

    // Set the initial total.

    updateTotal();

});
