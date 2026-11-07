# AmSaJ Used Laptops - starter project

A "hello world" Django site running in Docker with a PostgreSQL database.

## What you need installed (once)
1. Git: https://git-scm.com/downloads
2. Docker Desktop: https://www.docker.com/products/docker-desktop (start it and wait until it says it is running)

## Run it
Open a terminal in this folder, then:

    docker compose up --build

Wait until you see "Starting development server at http://0.0.0.0:8000/".
Open http://localhost:8000 in your browser. You should see "Hello world",
the Django version and "Database connection: OK".

Stop it with Ctrl+C. Start it again later with `docker compose up`.

## Create your admin login
In a second terminal, in the same folder:

    docker compose exec web python manage.py createsuperuser

Then open http://localhost:8000/admin/ and sign in.

## Phase 2: laptop inventory and photo/video admin
After pulling in this version, rebuild once (new libraries were added):

    docker compose up --build

Then, in a second terminal:

    docker compose exec web python manage.py seed_brands

Open http://localhost:8000/admin/ and use **Inventory > Laptops**:
1. Add laptop: fill the specs, price and condition note, then Save and continue editing.
2. Click **Upload multiple photos / videos** (top right) and select all files at once.
   Photos: JPG, PNG, WebP, HEIC (max 10 MB). Videos: MP4 (max 100 MB).
3. Set the status to Available. A laptop needs at least one photo to be Available.

Handy list actions: mark Available/Draft/Under investigation/Returned, duplicate, export CSV.
Run the automated tests with:  docker compose exec web python manage.py test

## Phase 3: customer storefront
No new libraries, so you only need to restart (rebuild is not required):

    docker compose up

Then open:
- http://localhost:8000/            home page (new arrivals, shop by brand)
- http://localhost:8000/laptops/    all laptops with filters, search and sorting
- http://localhost:8000/returns-and-warranty/

Only laptops with status Available or Reserved appear in the listing. Sold laptops keep a
product page marked "Sold". Draft, Under investigation and Returned laptops are never shown.


## Phase 4: cart, checkout and orders
After copying this version in, restart with:

    docker compose up --build

Then, in a second terminal, run these once:

    docker compose exec web python manage.py migrate
    docker compose exec web python manage.py seed_shipping

(`migrate` also runs automatically when the site starts.) Open http://localhost:8000/admin/ and
edit **Orders > Shipping zones** and **Pickup locations**: set your real delivery fees, delivery
times, pickup address and schedule. Put your bank details in a `.env` file (see `.env.example`)
so customers who choose bank transfer can see them.

Customer flow: laptop page > Add to cart > Cart > Checkout > order page.
- Card: goes to a TEST payment page (no real money). A real gateway is added later.
- Bank transfer / Cash on delivery: staff confirm the order in the admin.
- The laptop is Reserved when the order is placed (15 minutes for card, 24 hours for the others)
  and goes back to Available automatically if the order is not paid or confirmed in time.

Staff flow: **Orders > Orders**, open an order, use the buttons at the top (Mark payment received,
Confirm cash on delivery, Start processing, Mark shipped, Mark delivered, Cancel, Mark refund paid).
Enter the courier and tracking number in the Shipment section and save before Mark shipped.
**Print invoice** opens a printable invoice and packing slip.

Returns: **Orders > Return cases > Add**, pick the order item. The laptop becomes Under
investigation. When resolved, use the list actions: "Fixed" puts it back to Available, "Not fixable"
sets it to Returned (sent back to the supplier).

Order emails are printed in the terminal (docker compose logs -f web) until real email is set up.

## Useful commands
- Stop and keep data:       docker compose down
- Stop and DELETE database: docker compose down -v
- View logs:                docker compose logs -f web
- Run any Django command:   docker compose exec web python manage.py <command>

## Project layout
- config/   project settings and URLs
- store/    the customer storefront (pages, filters, templates, styles)
- inventory/ laptops, photos/videos and the admin screens
- Dockerfile, docker-compose.yml   how the system is packaged and run
