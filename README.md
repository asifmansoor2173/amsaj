# AmSaJ Used Laptops - starter project

A  Django site running in Docker with a PostgreSQL database used for Sale of Old laptops

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

Pickup locations: add them in **Orders > Pickup locations** (name, address, pickup schedule) and tick
**Active**. Customers who choose Pickup at checkout pick one of the active locations and see its address
and schedule. With no active location, the Pickup option is hidden. The starter location created by
`seed_shipping` is switched off until you fill it in and tick Active.

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

## Phase 5: activity logging and uniform photos

Every activity is recorded in two places with the same information:
1. **Log file** `logs/activity.log` in your project folder: one JSON line per event (problems also go to
   `logs/app.log`). Files rotate at 5 MB. The `logs` folder is not sent to Git.
2. **Database table** shown in the admin as **Activity log > Activity log entries**: time, level, action,
   who did it, IP address, page, order number, laptop code and a details box with the values.

Each row has two ids:
- **Event id**: unique for that single row.
- **Transaction id** (`TXN-...`): shared by the whole story. A customer's transaction starts at the first
  "Add to cart" click and continues through checkout, payment, shipping, delivery and any return. Staff
  actions on an order join the same transaction. Other staff activity gets its own id per request.

Find the whole story of an order:
- Admin: open the order and click **Activity trace**, or Activity log > **Trace an order or transaction**
  and type an order number, transaction id, laptop code (AMS-000001) or event id.
- Terminal: `docker compose exec web python manage.py trace ORD-000001`

What is logged: page requests, cart changes, checkout problems, orders, every order status change,
payments (success, failure, refund due), laptop status changes with the reason, emails, returns, photo
uploads, staff sign-in and sign-out and failed sign-ins, and every staff create/change/delete with old and
new values, and errors. Entries cannot be edited or deleted from the admin. Customer email and phone are
masked (r***@example.com, ******3210), passwords and private order links are never logged.
Settings in `config/settings.py`: `ACTIVITY_LOG_REQUESTS = False` stops page-request rows. Times use the
timezone in settings (Asia/Kolkata). To delete old rows on purpose:
`docker compose exec web python manage.py purge_activity_logs --days 365` (never runs by itself).

Uniform photos: every photo is now saved at exactly 1200x900 (thumbnails 400x300), shrunk to fit and
centred on white so nothing is cropped. Photos uploaded earlier are resized by
`docker compose exec web python manage.py regenerate_images` (run automatically by deploy.ps1).

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
