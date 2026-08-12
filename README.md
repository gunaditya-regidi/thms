# LRDC 2026 Treasure Hunt Management System (THMS)

A mobile-first, production-ready real-time web application designed to orchestrate and manage high-concurrency treasure hunt events. It supports balanced house assignments, task verification workflows for Round 1, sequential QR clue validation for Round 2, and real-time dashboard updates via WebSockets.

---

## 🗺️ Game Workflow & Rules

The game is divided into registration, Round 1 (Task Verification), and Round 2 (Sequential Clue Scan).

```mermaid
graph TD
    A[Registration] -->|Automatic Balanced Assignment| B(House Assigned: Red/Blue/Green/Yellow)
    B --> C[Round 1: Task Verification]
    C -->|9 Group Challenges + Photo Upload| D[Submit for Verification]
    D -->|House Manager Approves| E[Round 2: Sequential Scan Hunt]
    E -->|Start Level 2 - Clue 1 Image Unlocked| F[Scan QR Station]
    F -->|Verify Previous Clue Passcode| G{Passcode Correct?}
    G -->|No| H[Wrong Passcode Warning]
    G -->|Yes| I[Unlock Current Clue Image]
    I -->|Locate Next Clue Station| F
    F -->|Clue 6 Solved| J[Level 7 Active]
    J -->|Passcode input for Clue 7| K[Submit Clue 6 Password]
    K -->|Correct| L[Hunt Completed & Finished]
```

### 1. Registration
* **Leader Registration:** Team Leaders register their team. Mobile numbers for the leader and all three additional members must be **exactly 10 digits** and unique across the database.
* **Balanced House Assignment:** Upon registering, the team is automatically assigned to one of the 4 houses (**Red, Green, Blue, Yellow**) maintaining strict color distribution balance.

### 2. Round 1 (Photo Tasks)
* Teams must complete **9 specific group tasks** (e.g. collecting colored objects, designing a team logo, recording videos).
* The team leader uploads proof images/videos and checks off completed tasks.
* Once all 9 tasks are marked, the leader submits the round for verification.
* The assigned **House Manager** reviews and approves the submission, advancing the team to Round 2 and starting their stopwatch.

### 3. Round 2 (Sequential QR Clue Hunt)
* **Level 1 (Auto-Solved):** Round 2 starts automatically at **Level 2**. The Clue 1 image (revealing the passcode to Clue 2 and its location hint) is instantly available on the dashboard.
* **Scanning Sequence:** To unlock subsequent clue images (up to Clue 6), teams must locate physical QR stations in the wild and scan them.
  * Scanning **Clue 2** requires entering **Clue 1's password** (obtained from the Clue 1 image).
  * Scanning **Clue X** requires entering **Clue X-1's password** (obtained from the Clue X-1 image).
* **Decoy Dummy Clues:** Decoy QR codes placed in the wild do not require any passcodes. Scanning them instantly registers a "Decoy Clue" event and displays a warning to keep searching.
* **Clue 7 Direct Unlock:** Scanning Clue 6 unlocks the Clue 6 image but does not end the hunt. It advances the team to Level 7. Since Clue 7 has no physical QR code, the team must enter the password obtained from Clue 6's image directly into a passcode input box on the dashboard to complete the hunt.

---

## 🏗️ Architecture & Data Model

The application utilizes a single-process WSGI event loop to support real-time Socket.io broadcasts (live leaderboard feeds) and prevent sqlite database lockouts under multi-client loads.

```mermaid
classDiagram
    class User {
        +id: Integer
        +username: String
        +role: String ('admin', 'house_manager', 'team_leader')
        +plain_password: String
    }
    class Team {
        +id: Integer
        +team_name: String
        +leader_name: String
        +leader_phone: String
        +current_round: Integer
        +round1_status: String
        +round2_current_clue: Integer
        +round2_completed: Boolean
        +round2_completion_time: DateTime
    }
    class House {
        +id: Integer
        +name: String ('Red', 'Blue', 'Green', 'Yellow')
    }
    class QRCode {
        +id: Integer
        +uuid: String
        +clue_number: Integer
        +password: String
        +is_dummy: Boolean
        +allowed_houses: String
    }
    class TaskCompletion {
        +id: Integer
        +team_id: Integer
        +task_id: Integer
    }
    class QRScanLog {
        +id: Integer
        +team_id: Integer
        +qr_code_id: Integer
        +is_correct: Boolean
        +is_dummy: Boolean
    }
    class SystemLog {
        +id: Integer
        +action: String
        +details: String
    }
    
    User "1" --> "0..1" Team : team_profile
    User "1" --> "0..1" House : manager_profile
    Team "many" --> "1" House : house assignment
    Team "1" --> "many" TaskCompletion
    Team "1" --> "many" QRScanLog
    QRCode "1" --> "many" QRScanLog
```

### Cheating & Single Device Protection
To prevent teams from bypassing the hunt by logging in on multiple devices simultaneously, the dashboard enforces **Active Device Session Locking**. Logging in on a new device automatically invalidates and logs out any existing active sessions for that team.

---

## 🛠️ Installation & Running Locally

### Prerequisites
* Python 3.9 or higher
* Node.js (Optional, for web building tools)

### 1. Install Dependencies
```bash
# Clone the repository
git clone https://github.com/gunaditya-regidi/thms.git
cd thms

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: .\venv\Scripts\activate

# Install required libraries
pip install -r requirements.txt
```

### 2. Seed Database
Seeding creates default administrative roles, house definitions, dummy decoy clues, and configures the default application state:
```bash
python seed.py
```

### 3. Run Development Server
```bash
python app.py
```
The app will run locally at **`http://127.0.0.1:5000`**.

---

## 🧪 Testing Suite

To execute the integration tests (verifying registration formats, stopwatch ticks, balanced house allocation, and Round 2 validation pipelines):
```bash
# Run clue validation test
python scratch/test_clue_access.py

# Run registration and login validations
python scratch/test_register_login.py
```

---

## 📊 Event Credentials & Portals

The `seed.py` script registers the following default administrative access accounts:

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

### 3. Public Stats Board
* **URL:** `/stats`
* **Password:** `nstl@321`

---

## 🖨️ PDF QR Clue Sheets Design

Admins can download standard printable sheets of the generated QR codes. The sheets are custom-styled to enforce the following guidelines:
* **Header:** Centered title `"LRDC 2026 TREASURE HUNT"` at the top.
* **Scale:** QR codes are rendered at **6.5 in × 6.5 in**, filling **70%** of the printable page width.
* **Tiny Identification:** Large clue numbers and house labels are removed. In their place, a tiny identification identifier (`< 5 pt`) is positioned at the bottom-right corner of each page for tracking.

---

## 💾 Changing Databases (PostgreSQL / MySQL)

To switch the database to a cloud-managed provider without altering application source code:
1. Provision a PostgreSQL or MySQL instance.
2. Set the `DATABASE_URL` environment variable:
   * **PostgreSQL:** `DATABASE_URL=postgresql://user:pass@host:5432/dbname`
   * **MySQL:** `DATABASE_URL=mysql+pymysql://user:pass@host:3306/dbname` (requires installing `pymysql`)
3. Run `python seed.py` to auto-compile schemas and populate default accounts.
