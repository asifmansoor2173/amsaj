import re

from django import forms

from .constants import STATE_CHOICES
from .models import Order, PickupLocation


class CheckoutForm(forms.Form):
    customer_name = forms.CharField(label="Full name", max_length=120)
    email = forms.EmailField()
    phone = forms.CharField(label="Mobile number", max_length=20)
    delivery_method = forms.ChoiceField(choices=Order.Delivery.choices, widget=forms.RadioSelect,
                                        initial=Order.Delivery.DELIVERY)
    address_line1 = forms.CharField(label="Address line 1", max_length=200, required=False)
    address_line2 = forms.CharField(label="Address line 2", max_length=200, required=False)
    city = forms.CharField(max_length=80, required=False)
    state = forms.ChoiceField(choices=[("", "Select state")] + STATE_CHOICES, required=False)
    pin_code = forms.CharField(label="PIN code", max_length=6, required=False)
    pickup_location = forms.ModelChoiceField(queryset=PickupLocation.objects.none(), required=False,
                                             empty_label="Select pickup location")
    payment_method = forms.ChoiceField(choices=Order.Method.choices, widget=forms.RadioSelect)
    customer_notes = forms.CharField(label="Notes (optional)", widget=forms.Textarea(attrs={"rows": 2}), required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        active = PickupLocation.objects.filter(active=True)
        self.fields["pickup_location"].queryset = active
        self.pickup_available = active.exists()
        if not self.pickup_available:
            # No pickup location is configured: only offer delivery.
            self.fields["delivery_method"].choices = [(Order.Delivery.DELIVERY, Order.Delivery.DELIVERY.label)]
        elif active.count() == 1:
            # A single location is chosen for the customer.
            self.fields["pickup_location"].initial = active.first().pk
            self.fields["pickup_location"].empty_label = None

    def clean_phone(self):
        digits = re.sub(r"\D", "", self.cleaned_data["phone"])
        if len(digits) == 12 and digits.startswith("91"):
            digits = digits[2:]
        if not re.fullmatch(r"[6-9]\d{9}", digits):
            raise forms.ValidationError("Enter a valid 10-digit Indian mobile number.")
        return digits

    def clean_pin_code(self):
        pin = self.cleaned_data["pin_code"].strip()
        if pin and not re.fullmatch(r"[1-9]\d{5}", pin):
            raise forms.ValidationError("Enter a valid 6-digit PIN code.")
        return pin

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("delivery_method") == Order.Delivery.PICKUP:
            if not cleaned.get("pickup_location"):
                self.add_error("pickup_location", "Choose where you will pick up the laptop.")
        else:
            for field in ("address_line1", "city", "state", "pin_code"):
                if not cleaned.get(field):
                    self.add_error(field, "This field is required for delivery.")
        return cleaned

    def order_data(self):
        data = dict(self.cleaned_data)
        location = data.pop("pickup_location", None)
        data["pickup_location_id"] = location.pk if location else None
        if data["delivery_method"] == Order.Delivery.DELIVERY:
            data["pickup_location_id"] = None
        if data["delivery_method"] == Order.Delivery.PICKUP:
            for field in ("address_line1", "address_line2", "city", "state", "pin_code"):
                data[field] = ""
        return data


class TrackForm(forms.Form):
    order_no = forms.CharField(label="Order number", max_length=20)
    phone = forms.CharField(label="Mobile number used for the order", max_length=20)
