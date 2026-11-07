import uuid

from django.db import migrations


def assign(apps, schema_editor):
    Order = apps.get_model("orders", "Order")
    for order in Order.objects.filter(transaction_id=""):
        order.transaction_id = "TXN-" + uuid.uuid4().hex[:12].upper()
        order.save(update_fields=["transaction_id"])


class Migration(migrations.Migration):
    dependencies = [("orders", "0002_order_transaction_id")]
    operations = [migrations.RunPython(assign, migrations.RunPython.noop)]
