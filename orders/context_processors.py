def cart(request):
    return {"cart_count": len(request.session.get("cart", []))}
