
document.addEventListener("DOMContentLoaded", () => {

    const form = document.getElementById("registration-form");

    if (!form) {
        return;
    }

    const totalElement = document.getElementById("total");
    const resultElement = document.getElementById("registration-result");

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

    // Prepare a submission preview.

    form.addEventListener("submit", event => {

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
                name: form.elements.name.value.trim(),
                email: form.elements.email.value.trim(),
                participant_type: participantType.value,
                home_tower: form.elements.home_tower.value.trim()
            },

            items: items

        };

        document.getElementById("registration-result-data").textContent =
            JSON.stringify(submission, null, 2);

        resultElement.hidden = false;

        resultElement.scrollIntoView({
            behavior: "smooth",
            block: "nearest"
        });

    });

    // Set the initial total.

    updateTotal();

});
