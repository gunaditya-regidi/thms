import random
from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app
from flask_login import login_user, logout_user, login_required, current_user
from models import db, User, Team, Member, House, SystemLog
from werkzeug.security import check_password_hash

auth_bp = Blueprint('auth', __name__)

def is_phone_unique(phone, exclude_team_id=None):
    """Checks if a phone number exists anywhere in the system (leaders or members)."""
    if not phone:
        return True
    
    # Check Team leaders
    leader_query = Team.query.filter_by(leader_phone=phone)
    if exclude_team_id:
        leader_query = leader_query.filter(Team.id != exclude_team_id)
    if leader_query.first():
        return False
        
    # Check members
    member_query = Member.query.filter_by(phone=phone)
    if exclude_team_id:
        member_query = member_query.join(Team).filter(Team.id != exclude_team_id)
    if member_query.first():
        return False
        
    return True

@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        if current_user.role == 'admin':
            return redirect(url_for('admin.dashboard'))
        elif current_user.role == 'manager':
            return redirect(url_for('manager.dashboard'))
        else:
            return redirect(url_for('team.dashboard'))

    if request.method == 'POST':
        team_name = request.form.get('team_name', '').strip()
        leader_name = request.form.get('leader_name', '').strip()
        leader_phone = request.form.get('leader_phone', '').strip()
        leader_password = request.form.get('leader_password', '').strip()
        
        # Capture member details
        members_data = []
        for i in range(2, 5):
            m_name = request.form.get(f'member{i}_name', '').strip()
            m_phone = request.form.get(f'member{i}_phone', '').strip()
            if m_name or m_phone:
                if not m_name or not m_phone:
                    flash(f"Please fill both Name and Phone for Member {i}.", "danger")
                    return render_template('auth/register.html')
                members_data.append((m_name, m_phone, i))

        # Basic validations
        if not team_name or not leader_name or not leader_phone or not leader_password:
            flash("All main team fields are required.", "danger")
            return render_template('auth/register.html')

        # Check unique Team Name / Username
        if User.query.filter_by(username=team_name).first() or Team.query.filter_by(team_name=team_name).first():
            flash("Team Name is already registered. Please choose another.", "danger")
            return render_template('auth/register.html')

        # Collect all phone numbers in the submission to check duplicates inside the submission
        all_submitted_phones = [leader_phone] + [m[1] for m in members_data]
        if len(all_submitted_phones) != len(set(all_submitted_phones)):
            flash("Duplicate phone numbers detected in registration form.", "danger")
            return render_template('auth/register.html')

        # Validate unique phones in database
        if not is_phone_unique(leader_phone):
            flash(f"Phone number {leader_phone} (Leader) is already registered.", "danger")
            return render_template('auth/register.html')
            
        for m_name, m_phone, m_idx in members_data:
            if not is_phone_unique(m_phone):
                flash(f"Phone number {m_phone} (Member {m_idx}) is already registered.", "danger")
                return render_template('auth/register.html')

        try:
            # Balanced House Allocation Algorithm
            houses = House.query.all()
            if not houses:
                flash("System houses are not configured. Please contact the administrator.", "danger")
                return render_template('auth/register.html')
            
            # Map house records to their current team counts
            house_counts = []
            for house in houses:
                count = Team.query.filter_by(house_id=house.id).count()
                house_counts.append((house, count))
            
            # Find the minimum count
            min_count = min(count for house, count in house_counts)
            
            # Filter houses that have exactly the minimum count
            least_populated_houses = [house for house, count in house_counts if count == min_count]
            
            # Randomly select from the list
            assigned_house = random.choice(least_populated_houses)
            
            # Create user for auth
            new_user = User(username=team_name, role='team_leader')
            new_user.set_password(leader_password)
            db.session.add(new_user)
            db.session.flush() # gets user.id

            # Create Team profile
            new_team = Team(
                user_id=new_user.id,
                team_name=team_name,
                leader_name=leader_name,
                leader_phone=leader_phone,
                house_id=assigned_house.id
            )
            db.session.add(new_team)
            db.session.flush() # gets team.id

            # Add Members
            for m_name, m_phone, m_idx in members_data:
                new_member = Member(
                    team_id=new_team.id,
                    name=m_name,
                    phone=m_phone,
                    member_index=m_idx
                )
                db.session.add(new_member)

            # Log registration
            log = SystemLog(
                action='registration',
                details=f"Team '{team_name}' registered. Assigned to House: {assigned_house.name}.",
                team_id=new_team.id,
                user_id=new_user.id,
                ip_address=request.remote_addr,
                browser=request.user_agent.string
            )
            db.session.add(log)
            db.session.commit()

            # Broadcast registration event for stats & real-time updates
            socketio = current_app.extensions.get('socketio')
            if socketio:
                socketio.emit('register_team', {
                    'team_id': new_team.id,
                    'team_name': team_name,
                    'house': assigned_house.name
                })

            flash(f"Registration successful! You have been assigned to the {assigned_house.name} House. Please login below.", "success")
            return redirect(url_for('auth.login'))
            
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error during registration: {str(e)}")
            flash("An error occurred during registration. Please try again.", "danger")
            return render_template('auth/register.html')

    return render_template('auth/register.html')

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        if current_user.role == 'admin':
            return redirect(url_for('admin.dashboard'))
        elif current_user.role == 'manager':
            return redirect(url_for('manager.dashboard'))
        else:
            return redirect(url_for('team.dashboard'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()

        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            login_user(user)
            
            # Log login audit
            team_id = user.team_profile.id if user.role == 'team_leader' else None
            log = SystemLog(
                action='login',
                details=f"User '{username}' logged in successfully.",
                user_id=user.id,
                team_id=team_id,
                ip_address=request.remote_addr,
                browser=request.user_agent.string
            )
            db.session.add(log)
            db.session.commit()

            # Redirect based on user role
            if user.role == 'admin':
                return redirect(url_for('admin.dashboard'))
            elif user.role == 'manager':
                return redirect(url_for('manager.dashboard'))
            elif user.role == 'team_leader':
                return redirect(url_for('team.dashboard'))
        else:
            flash("Invalid username or password.", "danger")

    return render_template('auth/login.html')

@auth_bp.route('/logout')
@login_required
def logout():
    username = current_user.username
    role = current_user.role
    team_id = current_user.team_profile.id if role == 'team_leader' else None
    
    # Log logout audit
    log = SystemLog(
        action='logout',
        details=f"User '{username}' logged out.",
        user_id=current_user.id,
        team_id=team_id,
        ip_address=request.remote_addr,
        browser=request.user_agent.string
    )
    db.session.add(log)
    db.session.commit()
    
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for('auth.login'))

# ==========================================
# REFRESHING QR CODE LOGIN ENDPOINTS
# ==========================================
import uuid
import datetime

# Temporary memory registry for QR login tokens
# Format: { token: { "status": "pending"|"approved", "user_id": None|int, "created_at": datetime } }
qr_login_sessions = {}

@auth_bp.route('/qr-login/init', methods=['GET'])
def qr_login_init():
    # Clean up expired tokens (older than 2 minutes)
    now = datetime.datetime.utcnow()
    expired = [t for t, data in qr_login_sessions.items() if (now - data['created_at']).total_seconds() > 120]
    for t in expired:
        qr_login_sessions.pop(t, None)

    token = str(uuid.uuid4())
    qr_login_sessions[token] = {
        'status': 'pending',
        'user_id': None,
        'created_at': now
    }
    return {'status': 'success', 'token': token}

@auth_bp.route('/qr-login/status/<token>', methods=['GET'])
def qr_login_status(token):
    session_data = qr_login_sessions.get(token)
    if not session_data:
        return {'status': 'expired', 'message': 'Login session expired or invalid.'}
    
    # Check if token is older than 2 minutes
    now = datetime.datetime.utcnow()
    if (now - session_data['created_at']).total_seconds() > 120:
        qr_login_sessions.pop(token, None)
        return {'status': 'expired', 'message': 'Login session expired.'}
        
    return {'status': session_data['status']}

@auth_bp.route('/scan-login/<token>', methods=['GET'])
def scan_login_page(token):
    session_data = qr_login_sessions.get(token)
    if not session_data:
        flash("QR code has expired. Please refresh the login page and scan again.", "danger")
        return redirect(url_for('auth.login'))
        
    # Check if token is expired
    now = datetime.datetime.utcnow()
    if (now - session_data['created_at']).total_seconds() > 120:
        qr_login_sessions.pop(token, None)
        flash("QR code has expired. Please refresh the login page and scan again.", "danger")
        return redirect(url_for('auth.login'))

    # If the user is NOT logged in on their mobile device, redirect to login
    # and pass the scan URL as the next redirect target
    if not current_user.is_authenticated:
        flash("Please sign in first to authorize this device.", "info")
        return redirect(url_for('auth.login', next=url_for('auth.scan_login_page', token=token)))
        
    # Render the authorization prompt screen
    return render_template('auth/qr_authorize.html', token=token, user=current_user)

@auth_bp.route('/scan-login/approve/<token>', methods=['POST'])
@login_required
def scan_login_approve(token):
    session_data = qr_login_sessions.get(token)
    if not session_data:
        return {'status': 'error', 'message': 'QR code has expired.'}
        
    now = datetime.datetime.utcnow()
    if (now - session_data['created_at']).total_seconds() > 120:
        qr_login_sessions.pop(token, None)
        return {'status': 'error', 'message': 'QR code has expired.'}
        
    # Approve the session
    session_data['status'] = 'approved'
    session_data['user_id'] = current_user.id
    
    # Emit SocketIO event to the room: login_<token>
    socketio = current_app.extensions.get('socketio')
    if socketio:
        socketio.emit('login_approved', {
            'status': 'approved',
            'token': token
        }, room=f"login_{token}")
        
    return {'status': 'success', 'message': 'Login authorized!'}

@auth_bp.route('/qr-login/finalize/<token>', methods=['POST'])
def qr_login_finalize(token):
    session_data = qr_login_sessions.get(token)
    if not session_data or session_data['status'] != 'approved' or not session_data['user_id']:
        return {'status': 'error', 'message': 'Session not authorized or expired.'}
        
    now = datetime.datetime.utcnow()
    if (now - session_data['created_at']).total_seconds() > 120:
        qr_login_sessions.pop(token, None)
        return {'status': 'error', 'message': 'Session expired.'}
        
    # Log the user in on this browser session!
    user = User.query.get(session_data['user_id'])
    if not user:
        return {'status': 'error', 'message': 'Authorized user not found.'}
        
    login_user(user)
    
    # Remove the token from our session store
    qr_login_sessions.pop(token, None)
    
    # Audit log
    team_id = user.team_profile.id if user.role == 'team_leader' else None
    log = SystemLog(
        action='login_qr',
        details=f"User '{user.username}' logged in via QR Code.",
        user_id=user.id,
        team_id=team_id,
        ip_address=request.remote_addr,
        browser=request.user_agent.string
    )
    db.session.add(log)
    db.session.commit()
    
    # Determine redirect target based on role
    redirect_url = url_for('team.dashboard')
    if user.role == 'admin':
        redirect_url = url_for('admin.dashboard')
    elif user.role == 'manager':
        redirect_url = url_for('manager.dashboard')
        
    return {'status': 'success', 'redirect_url': redirect_url}
