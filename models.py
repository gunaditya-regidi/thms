import datetime
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()

class User(db.Model, UserMixin):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    plain_password = db.Column(db.String(255), nullable=True)
    role = db.Column(db.String(20), nullable=False) # 'admin', 'manager', 'team_leader'
    is_active = db.Column(db.Boolean, default=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
        self.plain_password = password

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class House(db.Model):
    __tablename__ = 'houses'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(20), unique=True, nullable=False) # 'Red', 'Green', 'Blue', 'Yellow'
    
    # Relationships
    teams = db.relationship('Team', backref='house', lazy=True)
    managers = db.relationship('Manager', backref='house', lazy=True)

    @property
    def team_count(self):
        return len(self.teams)


class Manager(db.Model):
    __tablename__ = 'managers'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    house_id = db.Column(db.Integer, db.ForeignKey('houses.id'), nullable=False)
    
    # Relationships
    user = db.relationship('User', backref=db.backref('manager_profile', uselist=False, cascade="all, delete-orphan"))


class Admin(db.Model):
    __tablename__ = 'admins'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    
    # Relationships
    user = db.relationship('User', backref=db.backref('admin_profile', uselist=False, cascade="all, delete-orphan"))


class Team(db.Model):
    __tablename__ = 'teams'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    team_name = db.Column(db.String(80), unique=True, nullable=False)
    leader_name = db.Column(db.String(100), nullable=False)
    leader_phone = db.Column(db.String(15), unique=True, nullable=False)
    house_id = db.Column(db.Integer, db.ForeignKey('houses.id'), nullable=False)
    
    current_round = db.Column(db.Integer, default=1) # 1 or 2
    round1_status = db.Column(db.String(30), default='pending_start') # 'pending_start', 'active', 'requested', 'verified', 'approved'
    round2_current_clue = db.Column(db.Integer, default=1) # 1 to 7. Clue 7 is the final clue.
    round2_completed = db.Column(db.Boolean, default=False)
    round2_completion_time = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)

    # Relationships
    user = db.relationship('User', backref=db.backref('team_profile', uselist=False, cascade="all, delete-orphan"))
    members = db.relationship('Member', backref='team', lazy=True, cascade="all, delete-orphan")
    task_completions = db.relationship('TaskCompletion', backref='team', lazy=True, cascade="all, delete-orphan")
    round1_approvals = db.relationship('Round1Approval', backref='team', lazy=True, cascade="all, delete-orphan")
    round2_progresses = db.relationship('Round2Progress', backref='team', lazy=True, cascade="all, delete-orphan")
    scan_logs = db.relationship('QRScanLog', backref='team', lazy=True, cascade="all, delete-orphan")


class Member(db.Model):
    __tablename__ = 'members'
    id = db.Column(db.Integer, primary_key=True)
    team_id = db.Column(db.Integer, db.ForeignKey('teams.id', ondelete='CASCADE'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    phone = db.Column(db.String(15), unique=True, nullable=False)
    member_index = db.Column(db.Integer, nullable=False) # 2, 3, 4


class Task(db.Model):
    __tablename__ = 'tasks'
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text, nullable=False)
    task_number = db.Column(db.Integer, nullable=False) # 1 to 10


class TaskCompletion(db.Model):
    __tablename__ = 'task_completions'
    id = db.Column(db.Integer, primary_key=True)
    team_id = db.Column(db.Integer, db.ForeignKey('teams.id', ondelete='CASCADE'), nullable=False)
    task_id = db.Column(db.Integer, db.ForeignKey('tasks.id', ondelete='CASCADE'), nullable=False)
    completed_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    verified_by_manager_id = db.Column(db.Integer, db.ForeignKey('managers.id'), nullable=False)
    
    # Relationships
    task = db.relationship('Task')
    manager = db.relationship('Manager')


class Round1Approval(db.Model):
    __tablename__ = 'round1_approvals'
    id = db.Column(db.Integer, primary_key=True)
    team_id = db.Column(db.Integer, db.ForeignKey('teams.id', ondelete='CASCADE'), nullable=False)
    requested_at = db.Column(db.DateTime, nullable=True) # when Team Leader requests completion
    verified_at = db.Column(db.DateTime, nullable=True) # when Manager marks verified
    verified_by_manager_id = db.Column(db.Integer, db.ForeignKey('managers.id'), nullable=True)
    approved_at = db.Column(db.DateTime, nullable=True) # when Admin approves (official R1 completion time)
    approved_by_admin_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)

    manager = db.relationship('Manager')
    admin = db.relationship('User')


class QRCode(db.Model):
    __tablename__ = 'qr_codes'
    id = db.Column(db.Integer, primary_key=True)
    uuid = db.Column(db.String(50), unique=True, nullable=False) # Unique scan token
    clue_number = db.Column(db.Integer, nullable=False) # Clue level (1 to 6)
    password = db.Column(db.String(80), nullable=False)
    hint = db.Column(db.Text, nullable=False)
    image_path = db.Column(db.String(255), nullable=True) # relative path to clue image
    is_dummy = db.Column(db.Boolean, default=False)
    allowed_houses = db.Column(db.String(100), nullable=True) # Comma-separated house names (e.g. "Red,Blue")


class Round2Progress(db.Model):
    __tablename__ = 'round2_progresses'
    id = db.Column(db.Integer, primary_key=True)
    team_id = db.Column(db.Integer, db.ForeignKey('teams.id', ondelete='CASCADE'), nullable=False)
    clue_number = db.Column(db.Integer, nullable=False) # 1 to 7
    completed_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    qr_code_id = db.Column(db.Integer, db.ForeignKey('qr_codes.id', ondelete='CASCADE'), nullable=False)
    
    qr_code = db.relationship('QRCode')


class QRScanLog(db.Model):
    __tablename__ = 'qr_scan_logs'
    id = db.Column(db.Integer, primary_key=True)
    team_id = db.Column(db.Integer, db.ForeignKey('teams.id', ondelete='CASCADE'), nullable=False)
    qr_code_id = db.Column(db.Integer, db.ForeignKey('qr_codes.id', ondelete='SET NULL'), nullable=True)
    scanned_token = db.Column(db.String(80), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    is_correct = db.Column(db.Boolean, default=False)
    is_repeated = db.Column(db.Boolean, default=False)
    is_dummy = db.Column(db.Boolean, default=False)
    ip_address = db.Column(db.String(45), nullable=True)
    browser = db.Column(db.String(255), nullable=True)

    qr_code = db.relationship('QRCode')


class Statistics(db.Model):
    __tablename__ = 'statistics'
    id = db.Column(db.Integer, primary_key=True)
    stat_key = db.Column(db.String(50), unique=True, nullable=False)
    stat_value = db.Column(db.Text, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)


class SystemLog(db.Model):
    __tablename__ = 'system_logs'
    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    action = db.Column(db.String(80), nullable=False) # 'registration', 'login', 'task_completion', 'scan', etc.
    details = db.Column(db.Text, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    team_id = db.Column(db.Integer, db.ForeignKey('teams.id', ondelete='SET NULL'), nullable=True)
    ip_address = db.Column(db.String(45), nullable=True)
    browser = db.Column(db.String(255), nullable=True)

    user = db.relationship('User')
    team = db.relationship('Team')
