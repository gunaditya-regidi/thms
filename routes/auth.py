import random
from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app, session
from flask_login import login_user, logout_user, login_required, current_user
from models import db, User, Team, Member, House, SystemLog
from werkzeug.security import check_password_hash

auth_bp = Blueprint('auth', __name__)

def is_id_card_unique(id_card, exclude_team_id=None):
    """Checks if an ID card number exists anywhere in the system (leaders or members)."""
    if not id_card:
        return True
    
    # Check Team leaders
    leader_query = Team.query.filter_by(leader_phone=id_card)
    if exclude_team_id:
        leader_query = leader_query.filter(Team.id != exclude_team_id)
    if leader_query.first():
        return False
        
    # Check members
    member_query = Member.query.filter_by(phone=id_card)
    if exclude_team_id:
        member_query = member_query.join(Team).filter(Team.id != exclude_team_id)
    if member_query.first():
        return False
        
    return True

is_phone_unique = is_id_card_unique

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
        leader_id_card = request.form.get('leader_phone', '').strip()
        leader_password = request.form.get('leader_password', '').strip()
        
        # Capture member details
        members_data = []
        for i in range(2, 5):
            m_name = request.form.get(f'member{i}_name', '').strip()
            m_id_card = request.form.get(f'member{i}_phone', '').strip()
            if not m_name or not m_id_card:
                flash(f"Please fill both Name and ID Card Number for Member {i}. All 3 team members are required.", "danger")
                return render_template('auth/register.html')
            members_data.append((m_name, m_id_card, i))

        # Basic validations
        if not team_name or not leader_name or not leader_id_card or not leader_password:
            flash("All main team fields are required.", "danger")
            return render_template('auth/register.html')

        # Validate max 6-digit numbers
        def is_valid_id_card(id_card):
            return 1 <= len(id_card) <= 6 and id_card.isdigit()

        if not is_valid_id_card(leader_id_card):
            flash("Leader ID card number must be a digit number with maximum 6 digits.", "danger")
            return render_template('auth/register.html')

        for m_name, m_id_card, m_idx in members_data:
            if not is_valid_id_card(m_id_card):
                flash(f"Member {m_idx} ID card number must be a digit number with maximum 6 digits.", "danger")
                return render_template('auth/register.html')

        # Check unique Team Name / Username
        if User.query.filter_by(username=team_name).first() or Team.query.filter_by(team_name=team_name).first():
            flash("Team Name is already registered. Please choose another.", "danger")
            return render_template('auth/register.html')

        # Collect all ID card numbers in the submission to check duplicates inside the submission
        all_submitted_ids = [leader_id_card] + [m[1] for m in members_data]
        if len(all_submitted_ids) != len(set(all_submitted_ids)):
            flash("Duplicate ID card numbers detected in registration form.", "danger")
            return render_template('auth/register.html')

        # Validate unique ID cards in database
        if not is_id_card_unique(leader_id_card):
            flash(f"ID card number {leader_id_card} (Leader) is already registered.", "danger")
            return render_template('auth/register.html')
            
        for m_name, m_id_card, m_idx in members_data:
            if not is_id_card_unique(m_id_card):
                flash(f"ID card number {m_id_card} (Member {m_idx}) is already registered.", "danger")
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
                leader_phone=leader_id_card,
                house_id=assigned_house.id
            )
            db.session.add(new_team)
            db.session.flush() # gets team.id

            # Add Members
            for m_name, m_id_card, m_idx in members_data:
                new_member = Member(
                    team_id=new_team.id,
                    name=m_name,
                    phone=m_id_card,
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
    if request.method == 'GET' and request.args.get('replaced') == '1':
        from flask_login import logout_user
        logout_user()
        session.clear()
        flash("Your account was logged in from another device or session expired.", "warning")
        return redirect(url_for('auth.login'))

    if request.method == 'GET' and current_user.is_authenticated:
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
            import uuid
            login_token = str(uuid.uuid4())
            user.current_login_token = login_token
            db.session.commit()
            
            login_user(user)
            session['login_token'] = login_token
            
            # Emit socket event to notify other clients about the session replacement
            socketio = current_app.extensions.get('socketio')
            if socketio:
                socketio.emit('session_replaced', {
                    'user_id': user.id,
                    'login_token': login_token
                })
            
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

    all_users = User.query.all()
    return render_template('auth/login.html', users=all_users)

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
    current_user.current_login_token = None
    db.session.add(log)
    db.session.commit()
    
    logout_user()
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for('auth.login'))



@auth_bp.route('/change-password', methods=['GET', 'POST'])
@login_required
def change_password():
    if request.method == 'POST':
        old_password = request.form.get('old_password')
        new_password = request.form.get('new_password')
        confirm_password = request.form.get('confirm_password')
        
        if not old_password or not new_password or not confirm_password:
            flash("All fields are required.", "danger")
            return redirect(url_for('auth.change_password'))
            
        if not current_user.check_password(old_password):
            flash("Incorrect current password.", "danger")
            return redirect(url_for('auth.change_password'))
            
        if new_password != confirm_password:
            flash("New passwords do not match.", "danger")
            return redirect(url_for('auth.change_password'))
            
        if len(new_password) < 4:
            flash("New password must be at least 4 characters long.", "danger")
            return redirect(url_for('auth.change_password'))
            
        # Update password
        current_user.set_password(new_password)
        
        # Log system event
        team_id = current_user.team_profile.id if current_user.role == 'team_leader' else None
        log = SystemLog(
            action='change_password',
            details=f"User '{current_user.username}' changed their password.",
            user_id=current_user.id,
            team_id=team_id,
            ip_address=request.remote_addr,
            browser=request.user_agent.string
        )
        db.session.add(log)
        db.session.commit()
        
        flash("Password changed successfully!", "success")
        
        # Redirect based on role
        if current_user.role == 'admin':
            return redirect(url_for('admin.dashboard'))
        elif current_user.role == 'manager':
            return redirect(url_for('manager.dashboard'))
        else:
            return redirect(url_for('team.dashboard'))
            
    return render_template('auth/change_password.html')
