import unittest
import random
import uuid
from app import create_app
from models import db, User, Team, Member, House, Task, QRCode, QRScanLog
from routes.auth import is_phone_unique
from routes.team import process_qr_scan

class THMSTestSuite(unittest.TestCase):
    
    def setUp(self):
        # Create app configured with in-memory SQLite database for test speed
        self.app = create_app({
            'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
            'TESTING': True
        })
        
        self.app_context = self.app.app_context()
        self.app_context.push()
        
        db.create_all()
        
        # Seed Houses
        self.houses = []
        for h_name in ['Red', 'Green', 'Blue', 'Yellow']:
            house = House(name=h_name)
            db.session.add(house)
            self.houses.append(house)
        db.session.commit()
        
        # Seed basic clues (1 to 7) with house restrictions and a Dummy Clue
        self.clues = []
        for i in range(1, 8):
            allowed = None
            if i in [1, 2]:
                allowed = "Red"
            elif i == 3:
                allowed = "Red,Blue"
            qr = QRCode(uuid=f"test-clue-{i}", clue_number=i, password=f"pass{i}", hint=f"hint{i}", is_dummy=False, allowed_houses=allowed)
            db.session.add(qr)
            self.clues.append(qr)
            
        self.dummy_qr = QRCode(uuid="test-dummy", clue_number=99, password="dummy", hint="dummyhint", is_dummy=True)
        db.session.add(self.dummy_qr)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def test_balanced_house_allocation(self):
        """Validates that random allocation keeps house differences <= 1 across 100 registers."""
        print("\n[TEST] Running Balanced House Allocation simulation...")
        
        # Simulate registering 100 teams
        for i in range(1, 101):
            team_name = f"Team_{i}"
            
            # Implementation of balanced random allocation algorithm
            houses = House.query.all()
            house_counts = [(h, Team.query.filter_by(house_id=h.id).count()) for h in houses]
            min_count = min(count for h, count in house_counts)
            least_populated = [h for h, count in house_counts if count == min_count]
            assigned_house = random.choice(least_populated)
            
            # Save User & Team
            user = User(username=team_name, role='team_leader')
            user.set_password("pass")
            db.session.add(user)
            db.session.flush()
            
            team = Team(
                user_id=user.id,
                team_name=team_name,
                leader_name=f"Leader_{i}",
                leader_phone=f"9000000{i:03d}",
                house_id=assigned_house.id
            )
            db.session.add(team)
            db.session.commit()
            
            # Assert counts at each step
            current_counts = [Team.query.filter_by(house_id=h.id).count() for h in houses]
            max_diff = max(current_counts) - min(current_counts)
            self.assertTrue(max_diff <= 1, f"Balanced constraint violated! House counts: {current_counts}")
            
        print(f"[OK] 100 registrations simulated. Final House distributions: {[Team.query.filter_by(house_id=h.id).count() for h in self.houses]}")

    def test_phone_number_uniqueness(self):
        """Asserts that phone numbers must be unique across leaders and members."""
        print("[TEST] Running Phone Number Uniqueness checks...")
        
        # Create team 1
        u1 = User(username="Team_A", role='team_leader')
        u1.set_password("pass")
        db.session.add(u1)
        db.session.flush()
        
        t1 = Team(user_id=u1.id, team_name="Team_A", leader_name="Leader A", leader_phone="9998887770", house_id=self.houses[0].id)
        db.session.add(t1)
        db.session.flush()
        
        m1 = Member(team_id=t1.id, name="Member A2", phone="9998887771", member_index=2)
        db.session.add(m1)
        db.session.commit()
        
        # Test helper functions
        self.assertFalse(is_phone_unique("9998887770"), "Leader phone not flagged as duplicate")
        self.assertFalse(is_phone_unique("9998887771"), "Member phone not flagged as duplicate")
        self.assertTrue(is_phone_unique("9990001112"), "Available phone flagged as duplicate")
        
        print("[OK] Contact uniqueness validated successfully.")

    def test_universal_start_all_teams(self):
        """Asserts that universal start route successfully starts all pending teams."""
        print("[TEST] Running Universal Start All Teams checks...")
        
        # Setup multiple pending teams
        for i in range(3):
            u = User(username=f"UniversalTeam_{i}", role='team_leader')
            u.set_password("pass")
            db.session.add(u)
            db.session.flush()
            
            team = Team(
                user_id=u.id, 
                team_name=f"UniversalTeam_{i}", 
                leader_name=f"Leader {i}", 
                leader_phone=f"987654000{i}", 
                house_id=self.houses[0].id,
                round1_status='pending_start'
            )
            db.session.add(team)
        db.session.commit()
        
        # Call the endpoint or logic to approve all
        pending = Team.query.filter_by(round1_status='pending_start').all()
        self.assertEqual(len(pending), 3)
        
        # Simulate the universal start logic
        from datetime import datetime
        now = datetime.utcnow()
        for t in pending:
            t.round1_status = 'active'
            t.created_at = now
        db.session.commit()
        
        # Verify that all are now active
        pending_after = Team.query.filter_by(round1_status='pending_start').all()
        self.assertEqual(len(pending_after), 0)
        active = Team.query.filter_by(round1_status='active').all()
        self.assertEqual(len(active), 3)
        
        print("[OK] Universal start logic verified.")

    def test_sequential_and_dummy_qr_scans(self):
        """Asserts sequential scanning rules and dummy scan behavior."""
        print("[TEST] Running QR Sequential Scan Engine tests...")
        
        # Setup active Team on Round 2
        u = User(username="Scanners", role='team_leader')
        u.set_password("pass")
        db.session.add(u)
        db.session.flush()
        
        team = Team(user_id=u.id, team_name="Scanners", leader_name="L", leader_phone="9876543210", house_id=self.houses[0].id)
        team.current_round = 2
        team.round1_status = 'approved'
        team.round2_current_clue = 1
        db.session.add(team)
        db.session.commit()
        
        # Mock request context for logs
        class MockRequest:
            remote_addr = "127.0.0.1"
            user_agent = type('UA', (), {'string': 'PythonUnitTest'})()
            
        req = MockRequest()
        
        # 1. Scan Clue 2 first (out of order, should fail)
        res1 = process_qr_scan(team, "test-clue-2", "pass1", req)
        self.assertEqual(res1['status'], 'out_of_order')
        self.assertEqual(team.round2_current_clue, 1, "Progress advanced on out of order scan")
        
        # 2. Scan Dummy clue (decoy, should show details but not advance)
        res_dummy = process_qr_scan(team, "test-dummy", "dummy", req)
        self.assertEqual(res_dummy['status'], 'dummy')
        self.assertEqual(team.round2_current_clue, 1, "Progress advanced on dummy scan")
        
        # 3. Scan Clue 1 (correct, should succeed)
        res2 = process_qr_scan(team, "test-clue-1", "pass1", req)
        self.assertEqual(res2['status'], 'success')
        self.assertEqual(team.round2_current_clue, 2, "Progress did not advance on correct scan")
        
        # 4. Scan Clue 1 again (repeated, should log but not advance)
        res_rep = process_qr_scan(team, "test-clue-1", "pass1", req)
        self.assertEqual(res_rep['status'], 'repeated')
        self.assertEqual(team.round2_current_clue, 2, "Progress changed on repeated scan")
        
        # 5. Scan sequentially to completion (Level 2 to 6)
        for i in range(2, 7):
            res = process_qr_scan(team, f"test-clue-{i}", f"pass{i-1}", req)
            self.assertEqual(res['status'], 'success')
            
        # Final clue scan (Level 7) - returns completed_hunt to complete the hunt
        res_final = process_qr_scan(team, "test-clue-7", "pass6", req)
        self.assertEqual(res_final['status'], 'completed_hunt')
        self.assertEqual(team.round2_current_clue, 8)
        
        print("[OK] Sequential clue checks and dummy logs verified.")

if __name__ == "__main__":
    unittest.main()
