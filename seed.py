import uuid
import os
import qrcode
from app import create_app
from models import db, User, House, Manager, Admin, Task, QRCode

def run_seed(drop_tables=True):
    if drop_tables:
        print("Recreating database tables...")
        db.drop_all()
        db.create_all()
    else:
        print("Ensuring database tables exist...")
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

    # 3. Seed House Managers with specified credentials
    managers_data = [
        ('Rahul', 'Red', 'aug13red'),
        ('Sayan Gosh', 'Blue', 'aug13blue'),
        ('Shyam', 'Yellow', 'aug13yellow'),
        ('Green', 'Green', 'aug13green')
    ]
    for name, house_name, password in managers_data:
        manager_user = User.query.filter_by(username=name).first()
        if not manager_user:
            manager_user = User(username=name, role='manager')
            manager_user.set_password(password)
            db.session.add(manager_user)
            db.session.commit()

            manager_profile = Manager(
                user_id=manager_user.id,
                house_id=house_objects[house_name].id
            )
            db.session.add(manager_profile)
            print(f"Seeded Manager: {name} ({house_name} House) / Password: {password}")
        else:
            manager_user.set_password(password)
            manager_profile = Manager.query.filter_by(user_id=manager_user.id).first()
            if manager_profile:
                manager_profile.house_id = house_objects[house_name].id
            else:
                manager_profile = Manager(
                    user_id=manager_user.id,
                    house_id=house_objects[house_name].id
                )
                db.session.add(manager_profile)
            print(f"Updated Manager: {name} ({house_name} House) / Password: {password}")
    db.session.commit()

    # 4. Seed 10 Round 1 Tasks
    tasks_data = [
        ("Human Statue", "The entire team must freeze in a funny pose for 30 seconds while one teammate takes a photo."),
        ("Campus Detective", "Find a specific but commonly overlooked object on campus, such as a particular notice board, sign, statue, or room number, and take a team photo with it."),
        ("Reverse Charades", "One team member guesses while the other 3 act together. Complete 3 words correctly within 60 seconds."),
        ("Paper Tower", "Using only 10 sheets of paper, build the tallest free-standing tower in 3 minutes."),
        ("Secret Message", "Solve a simple coded message provided by the game master. The decoded phrase reveals the next location/clue."),
        ("Campus Relay", "One member runs to a designated point, memorizes a 5–7 word sentence, returns, and dictates it to the team. No writing during the run."),
        ("Emoji Challenge", "Identify 5 movies/songs/college-related phrases represented only by emojis."),
        ("Object Hunt", "Find 5 different objects matching clues such as “something blue,” “something circular,” “something older than 10 years,” etc., and photograph them."),
        ("Human Alphabet", "The team must physically form 3 letters using their bodies. Game master must approve the formation."),
        ("Final Puzzle", "Give the team 3 seemingly unrelated clues. They must identify the common connection and give the correct answer to receive the final treasure-hunt clue.")
    ]
    
    for index, (title, desc) in enumerate(tasks_data, start=1):
        task = Task.query.filter_by(task_number=index).first()
        if not task:
            task = Task(title=title, description=desc, task_number=index)
            db.session.add(task)
            print(f"Seeded Task {index}: {title}")
        else:
            task.title = title
            task.description = desc
            print(f"Updated Task {index}: {title}")
    db.session.commit()

    # 5. Seed Clue levels ONLY if no QRs exist in the database (preserves custom admin configs)
    if QRCode.query.first() is None:
        print("Seeding initial default clue levels...")
        qr_data = [
            (1, "r2-red-l1", "Behind the central lawn sun dial.", "uuid-l1-red", "Red", "Red 1.jpg"),
            (1, "r2-green-l1", "On the glass panel of the IT department server room.", "uuid-l1-green", "Green", "Green 1.jpg"),
            (1, "r2-blue-l1", "Under the water cooler in the Mechanical block ground floor.", "uuid-l1-blue", "Blue", "BLUE 1.jpg"),
            (1, "r2-yellow-l1", "Back of the seminar hall podium structure.", "uuid-l1-yellow", "Yellow", "YELLOW 1.jpg"),
            
            (2, "r2-red-l2", "Behind the central lawn sun dial (Red/Blue Area).", "uuid-l2-red", "Red", "Red 2.jpg"),
            (2, "r2-green-l2", "Attached to the solar panel charging station near the gym (Green/Yellow Area).", "uuid-l2-green", "Green", "Green 2.jpg"),
            (2, "r2-blue-l2", "Behind the central lawn sun dial (Red/Blue Area).", "uuid-l2-blue", "Blue", "BLUE 2.jpg"),
            (2, "r2-yellow-l2", "Attached to the solar panel charging station near the gym (Green/Yellow Area).", "uuid-l2-yellow", "Yellow", "YELLOW 2.jpg"),
            
            (3, "r2-shared-rb-l3", "Under the reception desk in the main administrative building.", "uuid-l3-redblue", "Red,Blue", "PURPLE.jpg"),
            (3, "r2-shared-gy-l3", "Under the reception desk in the main administrative building.", "uuid-l3-greenyellow", "Green,Yellow", "ORANGE.jpg"),
            
            (4, "r2-shared-l4", "Under the stone bench near the cafeteria gazebo.", "uuid-l4-shared", None, "BLACK 1.jpg"),
            (5, "r2-shared-l5", "Search near the IT department block.", "uuid-l5-shared", None, "BLACK 2.jpg"),
            (6, "r2-shared-l6", "Search near the central plaza.", "uuid-l6-shared", None, "BLACK 3.jpg"),
            (7, "r2-final-l7", "Completed! Report to your House Manager.", "uuid-l7-shared", None, "BLACK FINAL.jpg")
        ]

        base_dir = os.path.abspath(os.path.dirname(__file__))
        qr_folder = os.path.join(base_dir, 'generated_qr')
        os.makedirs(qr_folder, exist_ok=True)
        base_url = os.environ.get('RENDER_EXTERNAL_URL', 'http://localhost:5000')

        for num, pwd, hint, fixed_uuid, allowed, img_path in qr_data:
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
            
            if num != 1:
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
            qr = QRCode(
                uuid=d_uuid,
                clue_number=99,
                password=d_pwd,
                hint=d_hint,
                is_dummy=True,
                image_path="DUMMY.jpg"
            )
            db.session.add(qr)
            
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
