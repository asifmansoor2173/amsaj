from inventory.models import Laptop


class Cart:
    """A shopping cart kept in the visitor's session (a list of laptop ids)."""

    KEY = "cart"

    def __init__(self, request):
        self.session = request.session
        self.ids = [int(i) for i in self.session.get(self.KEY, [])]

    def _save(self):
        self.session[self.KEY] = self.ids
        self.session.modified = True

    def add(self, pk):
        if pk not in self.ids:
            self.ids.append(pk)
            self._save()
            return True
        return False

    def remove(self, pk):
        if pk in self.ids:
            self.ids.remove(pk)
            self._save()

    def clear(self):
        self.ids = []
        self._save()

    def __len__(self):
        return len(self.ids)

    def cleaned(self):
        """Return (laptops still available, titles of laptops that were dropped)."""
        laptops = {l.pk: l for l in Laptop.objects.filter(pk__in=self.ids)
                   .select_related("brand").prefetch_related("media")}
        keep, dropped = [], []
        for pk in self.ids:
            laptop = laptops.get(pk)
            if laptop and laptop.status == Laptop.Status.AVAILABLE:
                keep.append(laptop)
            elif laptop:
                dropped.append(f"{laptop.brand} {laptop.model}")
        if len(keep) != len(self.ids):
            self.ids = [l.pk for l in keep]
            self._save()
        return keep, dropped
