from decimal import Decimal, InvalidOperation

from django.core.paginator import Paginator
from django.db import connection
from django.db.models import Count, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, render

from inventory.models import Brand, Laptop

PAGE_SIZE = 12
LISTED = [Laptop.Status.AVAILABLE, Laptop.Status.RESERVED]   # shown in the shop listing
VIEWABLE = LISTED + [Laptop.Status.SOLD]                      # product page still opens

SORTS = {
    "newest": ("Newest first", "-created_at"),
    "price_asc": ("Price: low to high", "price"),
    "price_desc": ("Price: high to low", "-price"),
    "ram_desc": ("RAM: high to low", "-ram_gb"),
}


def shop_queryset():
    return Laptop.objects.select_related("brand").prefetch_related("media")


def health(request):
    """Simple check used to confirm the site and database are up."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        cursor.fetchone()
    return HttpResponse("ok")


def home(request):
    qs = shop_queryset().filter(status__in=LISTED)
    brands = (Brand.objects.filter(laptop__status__in=LISTED)
              .annotate(total=Count("laptop")).order_by("name"))
    return render(request, "store/home.html", {
        "new_arrivals": qs.order_by("-created_at")[:8],
        "brands": brands,
    })


def _decimal(value):
    try:
        return Decimal(value)
    except (InvalidOperation, TypeError):
        return None


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def laptop_list(request):
    base = Laptop.objects.filter(status__in=LISTED)
    qs = shop_queryset().filter(status__in=LISTED)
    get = request.GET

    q = get.get("q", "").strip()
    if q:
        for word in q.split():
            qs = qs.filter(Q(title__icontains=word) | Q(model__icontains=word) | Q(cpu__icontains=word)
                           | Q(gpu__icontains=word) | Q(brand__name__icontains=word) | Q(sku__icontains=word))

    brand_ids = [i for i in (_int(b) for b in get.getlist("brand")) if i]
    if brand_ids:
        qs = qs.filter(brand_id__in=brand_ids)

    ram_values = [i for i in (_int(r) for r in get.getlist("ram")) if i]
    if ram_values:
        qs = qs.filter(ram_gb__in=ram_values)

    storage_types = [t for t in get.getlist("storage_type") if t in Laptop.StorageType.values]
    if storage_types:
        qs = qs.filter(storage_type__in=storage_types)

    min_storage = _int(get.get("min_storage"))
    if min_storage:
        qs = qs.filter(storage_gb__gte=min_storage)

    screens = [s for s in (_decimal(x) for x in get.getlist("screen")) if s]
    if screens:
        qs = qs.filter(screen_inches__in=screens)

    min_price, max_price = _decimal(get.get("min_price")), _decimal(get.get("max_price"))
    if min_price is not None:
        qs = qs.filter(price__gte=min_price)
    if max_price is not None:
        qs = qs.filter(price__lte=max_price)

    sort = get.get("sort", "newest")
    if sort not in SORTS:
        sort = "newest"
    qs = qs.order_by(SORTS[sort][1], "-id")

    page = Paginator(qs, PAGE_SIZE).get_page(get.get("page"))

    context = {
        "page": page,
        "total": page.paginator.count,
        "q": q,
        "sort": sort,
        "sorts": [(key, label) for key, (label, _) in SORTS.items()],
        # Filter choices come from what is actually in stock.
        "brand_choices": Brand.objects.filter(laptop__status__in=LISTED).distinct().order_by("name"),
        "ram_choices": sorted(set(base.values_list("ram_gb", flat=True))),
        "storage_type_choices": sorted(set(base.values_list("storage_type", flat=True))),
        "screen_choices": sorted(set(base.values_list("screen_inches", flat=True))),
        "selected": {
            "brand": [str(i) for i in brand_ids],
            "ram": [str(i) for i in ram_values],
            "storage_type": storage_types,
            "screen": get.getlist("screen"),
            "min_storage": str(min_storage or ""),
            "min_price": get.get("min_price", ""),
            "max_price": get.get("max_price", ""),
        },
        "filters_active": any([q, brand_ids, ram_values, storage_types, min_storage, screens,
                               min_price is not None, max_price is not None]),
    }
    return render(request, "store/laptop_list.html", context)


def laptop_detail(request, pk):
    laptop = get_object_or_404(shop_queryset(), pk=pk)
    if laptop.status not in VIEWABLE:
        raise Http404("This laptop is not available.")
    related = (shop_queryset().filter(status=Laptop.Status.AVAILABLE, brand=laptop.brand)
               .exclude(pk=laptop.pk).order_by("-created_at")[:4])
    return render(request, "store/laptop_detail.html", {"laptop": laptop, "related": related})


def returns_and_warranty(request):
    return render(request, "store/returns_warranty.html")
