# Treasure Hunt Management System (THMS)

A mobile-first, production-ready real-time web application designed to manage college/school treasure hunt events. It supports balanced house assignments, task verification workflows for Round 1, sequential QR clue validation for Round 2, and real-time dashboard updates via WebSockets.

---

## Technical Stack
* **Frontend:** HTML5, CSS3 (Glassmorphism & animations), Bootstrap 5, Bootstrap Icons, Chart.js (analytics), html5-qrcode (camera scanner), Socket.io Client.
* **Backend:** Python 3, Flask, Flask-SQLAlchemy (ORM), Flask-Login (session management), Flask-SocketIO (WebSockets).
* **Database:** SQLite (default) / Switchable to PostgreSQL or MySQL.
* **Deployment:** Preconfigured for instant Render or Railway deployment (WSGI via Gunicorn and Eventlet).

---

## Deployment Architecture

The system is configured to run as a single-process WSGI container using `gunicorn` with `eventlet` asynchronous workers. This setup guarantees that Socket.io real-time statistics broadcasts, state tracking, and countdown overlays operate reliably without needing a separate Redis message broker.

---

## Event Accounts & Credentials

The seed script (`seed.py`) configures the following default access accounts:

### 1. System Administrator
* **URL:** `/auth/login` (Auto-redirects to `/admin`)
* **Username:** `admin@nstl`
* **Password:** `treasure@lrdc2026`

### 2. House Managers
* **URL:** `/auth/login` (Auto-redirects to `/manager`)
* **Credentials:** (Username and Password are identical)
  * Red House Manager: `Aditya` / `Aditya`
  * Green House Manager: `Shyam` / `Shyam`
  * Blue House Manager: `Rahul` / `Rahul`
  * Yellow House Manager: `Saranya` / `Saranya`

### 3. Public Statistics & Leaderboard Gate
* **URL:** `/stats`
* **Password:** `nstl@321`

---

## Database Switch (SQLite to PostgreSQL/MySQL)

To switch the database to a hosted service without changing any application code:
1. Setup a PostgreSQL or MySQL database.
2. Set the `DATABASE_URL` environment variable (or write it to a `.env` file):
   * **PostgreSQL Example:** `DATABASE_URL=postgresql://username:password@hostname:5432/dbname`
   * **MySQL Example:** `DATABASE_URL=mysql+pymysql://username:password@hostname:3306/dbname` (requires `pymysql` package).
3. Run `python seed.py` to compile tables and insert seed records in the new database.

---

## Deployment on Render (WebSocket Enabled)

Render is the recommended hosting platform. The project is pre-configured with a `render.yaml` file for automatic deployment:

### Free Tier Deploy
1. Push your repository to GitHub.
2. Create a new **Web Service** on Render.
3. Connect your GitHub repository.
4. Fill in the configurations:
   * **Build Command:** `pip install -r requirements.txt && python seed.py`
   * **Start Command:** `gunicorn --worker-class eventlet -w 1 --bind 0.0.0.0:$PORT "app:create_app()"`
5. Under **Environment Variables**, add:
   * `STATS_PASSWORD` = `nstl@321`
   * `SECRET_KEY` = (A secure random string)
6. Trigger the build. The app will boot up. 
*Note: On the free tier, SQLite database records and uploaded images will be reset when the container sleeps or restarts.*

### Production Tier Deploy (Highly Recommended for Event Day)
To prevent SQLite resets and maintain complete persistence:
1. Upgrade the web service type to **Starter** (approx. $7/month).
2. Under **Disks**, click **Add Disk**:
   * **Name:** `sqlite-data`
   * **Mount Path:** `/opt/render/project/src` (this maps to your root folder, persisting the SQLite database).
   * **Size:** `1 GB` (more than enough for SQLite logs).
3. Redeploy. Your SQLite database is now 100% safe.
