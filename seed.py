import uuid
import os
import qrcode
from app import create_app
from models import db, User, House, Manager, Admin, Task, QRCode

def run_seed():
    print("Recreating database tables...")
    db.drop_all()
    db.create_all()

    # 1. Seed Houses
    houses = ['Red', 'Green', 'Blue', 'Yellow']
    house_objects = {}
    for h_name in houses:
        house = House.query.filter_by(name=h_name).first()
        if not house:
            house = House(name=h_name)
            db.session.add(house)
            print(f"Seeded House: {h_name}")
        house_objects[h_name] = house
    db.session.commit()

    # 2. Seed Admin
    admin_user = User.query.filter_by(username='admin@nstl').first()
    if not admin_user:
        admin_user = User(username='admin@nstl', role='admin')
        admin_user.set_password('treasure@lrdc2026')
        db.session.add(admin_user)
        db.session.commit()
        
        admin_profile = Admin(user_id=admin_user.id)
        db.session.add(admin_profile)
        print("Seeded Admin: admin@nstl / treasure@lrdc2026")
    db.session.commit()

    # 3. Seed House Managers
    managers_data = [
        ('Aditya', 'Red'),
        ('Shyam', 'Green'),
        ('Rahul', 'Blue'),
        ('Saranya', 'Yellow')
    ]
    for name, house_name in managers_data:
        manager_user = User.query.filter_by(username=name).first()
        if not manager_user:
            manager_user = User(username=name, role='manager')
            manager_user.set_password(name) # UID and PWD are same as name
            db.session.add(manager_user)
            db.session.commit()

            manager_profile = Manager(
                user_id=manager_user.id,
                house_id=house_objects[house_name].id
            )
            db.session.add(manager_profile)
            print(f"Seeded Manager: {name} ({house_name} House)")
    db.session.commit()

    # 4. Seed 10 Round 1 Tasks
    tasks_data = [
        ("Team Registration & Briefing", "Verify team details, collect event kits, and attend the safety briefing."),
        ("Notice Board Decryption", "Locate the main library notice board, find the cipher message, and decrypt it."),
        ("Mechanical Equilibrium", "Balance 3 objects of different weights using simple items in the Physics lab."),
        ("Historic Publication Hunt", "Find the oldest book in the main library archive and write down its publication year."),
        ("Golden Ratio Structure", "Locate the architecture block, find the golden ratio design, and click a group photo."),
        ("Mystery Compound Identification", "Perform the chemical test at Chemistry Lab 3 and identify the compound."),
        ("Founder's Statue Inscription", "Find the main statue of the founder and copy down the exact date engraved on the plaque."),
        ("Morse Code Translation", "Listen to the audio Morse code broadcast in the Seminar Hall and write the message."),
        ("Logical Sequence Puzzle", "Solve the sequence challenge posted near the IT Department block."),
        ("Final Alignment Test", "Gather as a team at the central plaza and complete the physical coordination challenge.")
    ]
    
    for index, (title, desc) in enumerate(tasks_data, start=1):
        task = Task.query.filter_by(task_number=index).first()
        if not task:
            task = Task(title=title, description=desc, task_number=index)
            db.session.add(task)
            print(f"Seeded Task {index}: {title}")
    db.session.commit()

    # 5. Seed 7 Round 2 Clue Levels (Level 1 & 2: house-specific, Level 3: Red/Blue & Green/Yellow shared, Level 4-7: single shared)
    qr_data = [
        # Level 1: Separate for each house
        (1, "r2-red-l1", "Behind the central lawn sun dial.", "uuid-l1-red", "Red", "Red 1.jpg"),
        (1, "r2-green-l1", "On the glass panel of the IT department server room.", "uuid-l1-green", "Green", "Green 1.jpg"),
        (1, "r2-blue-l1", "Under the water cooler in the Mechanical block ground floor.", "uuid-l1-blue", "Blue", "BLUE 1.jpg"),
        (1, "r2-yellow-l1", "Back of the seminar hall podium structure.", "uuid-l1-yellow", "Yellow", "YELLOW 1.jpg"),
        
        # Level 2: Separate for each house
        (2, "r2-red-l2", "Behind the central lawn sun dial (Red/Blue Area).", "uuid-l2-red", "Red", "Red 2.jpg"),
        (2, "r2-green-l2", "Attached to the solar panel charging station near the gym (Green/Yellow Area).", "uuid-l2-green", "Green", "Green 2.jpg"),
        (2, "r2-blue-l2", "Behind the central lawn sun dial (Red/Blue Area).", "uuid-l2-blue", "Blue", "BLUE 2.jpg"),
        (2, "r2-yellow-l2", "Attached to the solar panel charging station near the gym (Green/Yellow Area).", "uuid-l2-yellow", "Yellow", "YELLOW 2.jpg"),
        
        # Level 3: Red and Blue same, Yellow and Green same
        (3, "r2-shared-rb-l3", "Under the reception desk in the main administrative building.", "uuid-l3-redblue", "Red,Blue", "PURPLE.jpg"),
        (3, "r2-shared-gy-l3", "Under the reception desk in the main administrative building.", "uuid-l3-greenyellow", "Green,Yellow", "ORANGE.jpg"),
        
        # Level 4: One single clue for all houses
        (4, "r2-shared-l4", "Under the stone bench near the cafeteria gazebo.", "uuid-l4-shared", None, "BLACK 1.jpg"),
        
        # Level 5: One single clue for all houses
        (5, "r2-shared-l5", "Search near the IT department block.", "uuid-l5-shared", None, "BLACK 2.jpg"),
        
        # Level 6: One single clue for all houses
        (6, "r2-shared-l6", "Search near the central plaza.", "uuid-l6-shared", None, "BLACK 3.jpg"),

        # Level 7: One single clue for all houses (Final)
        (7, "r2-final-l7", "Completed! Report to your House Manager.", "uuid-l7-shared", None, "BLACK FINAL.jpg")
    ]

    base_dir = os.path.abspath(os.path.dirname(__file__))
    qr_folder = os.path.join(base_dir, 'generated_qr')
    os.makedirs(qr_folder, exist_ok=True)
    base_url = os.environ.get('RENDER_EXTERNAL_URL', 'http://localhost:5000')

    for num, pwd, hint, fixed_uuid, allowed, img_path in qr_data:
        qr = QRCode.query.filter_by(uuid=fixed_uuid).first()
        if not qr:
            qr = QRCode(
                uuid=fixed_uuid,
                clue_number=num,
                password=pwd,
                hint=hint,
                is_dummy=False,
                allowed_houses=allowed,
                image_path=img_path
            )
            db.session.add(qr)
            print(f"Seeded QR Level {num} ({allowed or 'All'}): {pwd}")
        
        # Always generate PNG for local loading
        scan_url = f"{base_url.rstrip('/')}/scan/{fixed_uuid}"
        img = qrcode.make(scan_url)
        filepath = os.path.join(qr_folder, f"qr_{fixed_uuid}.png")
        img.save(filepath)
    db.session.commit()

    # 6. Seed 3 Dummy QR codes
    dummy_qrs = [
        ("dummy-uuid-1", "fake-pwd-1", "This clue seems to lead to a dead end. Keep looking!"),
        ("dummy-uuid-2", "fake-pwd-2", "Incorrect direction. Try inspecting other coordinates."),
        ("dummy-uuid-3", "fake-pwd-3", "Wrong clue! The pirate flag waves here, but the treasure lies elsewhere.")
    ]
    
    for d_uuid, d_pwd, d_hint in dummy_qrs:
        qr = QRCode.query.filter_by(uuid=d_uuid).first()
        if not qr:
            qr = QRCode(
                uuid=d_uuid,
                clue_number=99,
                password=d_pwd,
                hint=d_hint,
                is_dummy=True,
                image_path="DUMMY.jpg"
            )
            db.session.add(qr)
            print(f"Seeded Dummy QR: {d_uuid}")
        
        # Always generate PNG for local loading
        scan_url = f"{base_url.rstrip('/')}/scan/{d_uuid}"
        img = qrcode.make(scan_url)
        filepath = os.path.join(qr_folder, f"qr_{d_uuid}.png")
        img.save(filepath)
    db.session.commit()
    
    print("Database seeding completed successfully!")

def seed_database():
    app = create_app({'TESTING': True})
    with app.app_context():
        run_seed()

if __name__ == "__main__":
    seed_database()
