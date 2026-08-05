import uuid
from app import create_app
from models import db, User, House, Manager, Admin, Task, QRCode

def seed_database():
    app = create_app()
    with app.app_context():
        print("Recreating database tables...")
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

        # 5. Seed 7 Round 2 QR codes
        qr_data = [
            (1, "r2-alpha-99", "Look under the stone bench near the cafeteria gazebo.", "c1-uuid-1111"),
            (2, "r2-beta-88", "Behind the central lawn sun dial.", "c2-uuid-2222"),
            (3, "r2-gamma-77", "On the glass panel of the IT department server room.", "c3-uuid-3333"),
            (4, "r2-delta-66", "Under the water cooler in the Mechanical block ground floor.", "c4-uuid-4444"),
            (5, "r2-epsilon-55", "Back of the seminar hall podium structure.", "c5-uuid-5555"),
            (6, "r2-zeta-44", "Attached to the solar panel charging station near the gym.", "c6-uuid-6666"),
            (7, "r2-omega-final", "Under the reception desk in the main administrative building.", "c7-uuid-7777")
        ]

        for num, pwd, hint, fixed_uuid in qr_data:
            qr = QRCode.query.filter_by(clue_number=num, is_dummy=False).first()
            if not qr:
                qr = QRCode(
                    uuid=fixed_uuid, # Seed with deterministic UUIDs for test convenience, Admin can generate custom ones too
                    clue_number=num,
                    password=pwd,
                    hint=hint,
                    is_dummy=False
                )
                db.session.add(qr)
                print(f"Seeded QR Clue {num}: {pwd}")
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
                    is_dummy=True
                )
                db.session.add(qr)
                print(f"Seeded Dummy QR: {d_uuid}")
        db.session.commit()
        
        print("Database seeding completed successfully!")

if __name__ == "__main__":
    seed_database()
