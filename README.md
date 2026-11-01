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
The Add to cart button is a placeholder until the ordering phase.

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
