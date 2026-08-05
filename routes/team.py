import datetime
from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify, current_app
from flask_login import login_required, current_user
from models import db, Team, Task, TaskCompletion, Round1Approval, QRCode, Round2Progress, QRScanLog, SystemLog

team_bp = Blueprint('team', __name__)

def check_team_role(func):
    """Decorator to ensure user is logged in as a Team Leader."""
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != 'team_leader':
            flash("Unauthorized. Please log in as a Team Leader.", "danger")
            return redirect(url_for('auth.login'))
        return func(*args, **kwargs)
    wrapper.__name__ = func.__name__
    return wrapper

@team_bp.route('/team')
@check_team_role
def dashboard():
    team = current_user.team_profile
    if not team:
        flash("Team profile not found.", "danger")
        logout_user()
        return redirect(url_for('auth.login'))

    # Load tasks and completions for Round 1
    tasks = Task.query.order_by(Task.task_number).all()
    completions = {tc.task_id: tc for tc in team.task_completions}
    
    # Calculate progress % for Round 1
    completed_tasks_count = len(completions)
    r1_progress_percent = int((completed_tasks_count / 10) * 100) if tasks else 0

    # Get active clues details for Round 2
    current_clue = None
    next_clue_hint = None
    if team.current_round == 2 and team.round1_status == 'approved':
        if team.round2_current_clue <= 7:
            # The team is currently looking for clue 'team.round2_current_clue'
            # Let's see what hint we can display.
            # To get to clue X, they must have scanned clue X-1.
            # For Clue 1, they get an initial hint.
            # Let's fetch the QRCode for the current clue they need to scan.
            current_clue = QRCode.query.filter_by(clue_number=team.round2_current_clue, is_dummy=False).first()
            
            # The previous clue gives them the hint for the current clue.
            # If they are on Clue 1, what is their hint?
            # We can have a starting clue hint from the Admin, or display the Hint from Clue 1 itself as "Starting Clue"
            if team.round2_current_clue == 1:
                next_clue_hint = "Start clue: Find the first station!"
            else:
                prev_clue = QRCode.query.filter_by(clue_number=team.round2_current_clue - 1, is_dummy=False).first()
                if prev_clue:
                    next_clue_hint = prev_clue.hint

    # Get latest scan log
    latest_scan = QRScanLog.query.filter_by(team_id=team.id).order_by(QRScanLog.timestamp.desc()).first()

    # Pass ISO timestamps for client stopwatch
    r1_app = Round1Approval.query.filter_by(team_id=team.id).first()
    approved_iso = r1_app.approved_at.isoformat() if (r1_app and r1_app.approved_at) else ""
    created_iso = team.created_at.isoformat() if team.created_at else ""
    finished_iso = team.round2_completion_time.isoformat() if team.round2_completion_time else ""

    return render_template('team/dashboard.html', 
                           team=team, 
                           tasks=tasks, 
                           completions=completions, 
                           completed_tasks_count=completed_tasks_count,
                           r1_progress_percent=r1_progress_percent,
                           current_clue=current_clue,
                           next_clue_hint=next_clue_hint,
                           latest_scan=latest_scan,
                           created_iso=created_iso,
                           approved_iso=approved_iso,
                           finished_iso=finished_iso)

@team_bp.route('/team/request-r1', methods=['POST'])
@check_team_role
def request_r1():
    team = current_user.team_profile
    
    # Verify they have completed all 10 tasks
    tasks_count = Task.query.count()
    completed_count = TaskCompletion.query.filter_by(team_id=team.id).count()
    
    if completed_count < tasks_count:
        flash("You must complete all 10 tasks before requesting verification.", "warning")
        return redirect(url_for('team.dashboard'))

    if team.round1_status != 'active':
        flash("Round 1 completion has already been requested or approved.", "info")
        return redirect(url_for('team.dashboard'))

    try:
        team.round1_status = 'requested'
        
        # Check if record exists
        approval = Round1Approval.query.filter_by(team_id=team.id).first()
        if not approval:
            approval = Round1Approval(team_id=team.id)
            db.session.add(approval)
        
        approval.requested_at = datetime.datetime.utcnow()
        
        # Add system log
        log = SystemLog(
            action='r1_requested',
            details=f"Team '{team.team_name}' requested Round 1 completion verification.",
            team_id=team.id,
            user_id=current_user.id,
            ip_address=request.remote_addr,
            browser=request.user_agent.string
        )
        db.session.add(log)
        db.session.commit()

        # SocketIO emit to house manager and admin
        socketio = current_app.extensions.get('socketio')
        if socketio:
            socketio.emit('r1_requested', {
                'team_id': team.id,
                'team_name': team.team_name,
                'house': team.house.name
            })

        flash("Round 1 completion requested! Please report to your House Manager for physical verification.", "success")
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error in requesting R1 verification: {str(e)}")
        flash("An error occurred. Please try again.", "danger")

    return redirect(url_for('team.dashboard'))

@team_bp.route('/scan/<uuid>')
def direct_scan(uuid):
    """Direct route for standard QR code reader apps scanning a URL."""
    if not current_user.is_authenticated or current_user.role != 'team_leader':
        # Store scanned code in session so they can submit after logging in
        session = request.environ.get('beaker.session') # standard flask session
        from flask import session as flask_session
        flask_session['pending_scan_token'] = uuid
        flash("Scan detected! Please log in as your Team Leader to register the clue.", "info")
        return redirect(url_for('auth.login'))

    team = current_user.team_profile
    if not team:
        flash("Logged in user does not represent a Team.", "danger")
        return redirect(url_for('auth.logout'))

    # Run the QR scan engine
    result = process_qr_scan(team, uuid, request)
    
    if result['status'] == 'success':
        flash(f"Success: {result['message']}", "success")
    elif result['status'] == 'completed_hunt':
        flash(f"CONGRATULATIONS: {result['message']}", "success")
    elif result['status'] == 'dummy':
        flash(f"DUMMY CLUE: {result['message']}", "warning")
    elif result['status'] == 'repeated':
        flash(f"INFO: {result['message']}", "info")
    else:
        flash(f"ERROR: {result['message']}", "danger")

    return redirect(url_for('team.dashboard'))

@team_bp.route('/api/scan_qr', methods=['POST'])
@login_required
def api_scan_qr():
    """AJAX endpoint for in-app scanner."""
    if current_user.role != 'team_leader':
        return jsonify({'status': 'error', 'message': 'Only Team Leaders can scan QRs.'}), 403

    team = current_user.team_profile
    if not team:
        return jsonify({'status': 'error', 'message': 'Team profile not found.'}), 400

    data = request.get_json() or {}
    token = data.get('token', '').strip()
    
    if not token:
        return jsonify({'status': 'error', 'message': 'No QR code token provided.'}), 400

    result = process_qr_scan(team, token, request)
    return jsonify(result)

def process_qr_scan(team, token, req):
    """Core QR verification logic."""
    timestamp = datetime.datetime.utcnow()
    ip = req.remote_addr
    ua = req.user_agent.string
    
    # 1. Fetch QR code
    qr = QRCode.query.filter_by(uuid=token).first()
    
    # 2. Check if invalid token
    if not qr:
        # Log invalid scan
        log = QRScanLog(
            team_id=team.id,
            scanned_token=token,
            timestamp=timestamp,
            is_correct=False,
            is_repeated=False,
            is_dummy=False,
            ip_address=ip,
            browser=ua
        )
        db.session.add(log)
        
        sys_log = SystemLog(
            action='scan_failed_invalid',
            details=f"Team '{team.team_name}' scanned an invalid QR token: {token[:20]}...",
            team_id=team.id,
            ip_address=ip,
            browser=ua
        )
        db.session.add(sys_log)
        db.session.commit()
        
        # Broadcast failed scan for dashboard logs
        socketio = current_app.extensions.get('socketio')
        if socketio:
            socketio.emit('qr_scanned', {
                'team_name': team.team_name,
                'house': team.house.name,
                'type': 'invalid',
                'timestamp': timestamp.strftime('%Y-%m-%d %H:%M:%S')
            })
            
        return {'status': 'invalid', 'message': 'Invalid QR Code scanned!'}

    # 3. Check if Dummy Clue
    if qr.is_dummy:
        log = QRScanLog(
            team_id=team.id,
            qr_code_id=qr.id,
            scanned_token=token,
            timestamp=timestamp,
            is_correct=False,
            is_repeated=False,
            is_dummy=True,
            ip_address=ip,
            browser=ua
        )
        db.session.add(log)
        
        sys_log = SystemLog(
            action='scan_dummy',
            details=f"Team '{team.team_name}' scanned Dummy Clue (Token: {token[:20]}).",
            team_id=team.id,
            ip_address=ip,
            browser=ua
        )
        db.session.add(sys_log)
        db.session.commit()

        socketio = current_app.extensions.get('socketio')
        if socketio:
            socketio.emit('qr_scanned', {
                'team_name': team.team_name,
                'house': team.house.name,
                'type': 'dummy',
                'timestamp': timestamp.strftime('%Y-%m-%d %H:%M:%S')
            })
            
        return {
            'status': 'dummy',
            'message': 'Wrong clue! This is a dummy station.',
            'hint': qr.hint,
            'password': qr.password,
            'image': qr.image_path or '/static/images/dummy-image.png'
        }

    # 4. Check Round 2 eligibility
    if team.current_round != 2 or team.round1_status != 'approved':
        return {'status': 'not_ready', 'message': 'You have not unlocked Round 2 yet. Please complete Round 1.'}

    # 5. Check if already finished Round 2
    if team.round2_completed:
        return {'status': 'already_finished', 'message': 'You have already completed all Round 2 clues!'}

    # 6. Check sequence validation
    # If the clue scanned has a number less than current expected clue, it's a repeated scan
    if qr.clue_number < team.round2_current_clue:
        log = QRScanLog(
            team_id=team.id,
            qr_code_id=qr.id,
            scanned_token=token,
            timestamp=timestamp,
            is_correct=False,
            is_repeated=True,
            is_dummy=False,
            ip_address=ip,
            browser=ua
        )
        db.session.add(log)
        
        sys_log = SystemLog(
            action='scan_repeated',
            details=f"Team '{team.team_name}' repeated scan of Clue {qr.clue_number}.",
            team_id=team.id,
            ip_address=ip,
            browser=ua
        )
        db.session.add(sys_log)
        db.session.commit()

        socketio = current_app.extensions.get('socketio')
        if socketio:
            socketio.emit('qr_scanned', {
                'team_name': team.team_name,
                'house': team.house.name,
                'type': 'repeated',
                'timestamp': timestamp.strftime('%Y-%m-%d %H:%M:%S'),
                'clue_number': qr.clue_number
            })
            
        return {
            'status': 'repeated',
            'message': f"You already solved Clue {qr.clue_number}.",
            'clue_number': qr.clue_number,
            'password': qr.password,
            'hint': qr.hint,
            'image': qr.image_path
        }

    # If the clue scanned has a number greater than current, they are skipping
    elif qr.clue_number > team.round2_current_clue:
        log = QRScanLog(
            team_id=team.id,
            qr_code_id=qr.id,
            scanned_token=token,
            timestamp=timestamp,
            is_correct=False,
            is_repeated=False,
            is_dummy=False,
            ip_address=ip,
            browser=ua
        )
        db.session.add(log)
        
        sys_log = SystemLog(
            action='scan_skipped',
            details=f"Team '{team.team_name}' scanned Clue {qr.clue_number} out of order (Expected Clue {team.round2_current_clue}).",
            team_id=team.id,
            ip_address=ip,
            browser=ua
        )
        db.session.add(sys_log)
        db.session.commit()

        socketio = current_app.extensions.get('socketio')
        if socketio:
            socketio.emit('qr_scanned', {
                'team_name': team.team_name,
                'house': team.house.name,
                'type': 'out_of_order',
                'timestamp': timestamp.strftime('%Y-%m-%d %H:%M:%S'),
                'clue_number': qr.clue_number
            })
            
        return {'status': 'out_of_order', 'message': f'Wrong clue! You cannot skip clues. You must find Clue {team.round2_current_clue} next.'}

    # 7. Correct QR scan
    try:
        log = QRScanLog(
            team_id=team.id,
            qr_code_id=qr.id,
            scanned_token=token,
            timestamp=timestamp,
            is_correct=True,
            is_repeated=False,
            is_dummy=False,
            ip_address=ip,
            browser=ua
        )
        db.session.add(log)
        
        # Save Round 2 progress
        progress = Round2Progress(
            team_id=team.id,
            clue_number=qr.clue_number,
            completed_at=timestamp,
            qr_code_id=qr.id
        )
        db.session.add(progress)
        
        # Update team expected clue
        team.round2_current_clue = team.round2_current_clue + 1
        
        # Check if they have scanned clue 7 (completion of Hunt)
        is_final = (qr.clue_number == 7)
        if is_final:
            team.round2_completed = True
            team.round2_completion_time = timestamp
            
            sys_log = SystemLog(
                action='hunt_completed',
                details=f"Team '{team.team_name}' successfully completed all Round 2 clues and finished the Hunt!",
                team_id=team.id,
                ip_address=ip,
                browser=ua
            )
            db.session.add(sys_log)
        else:
            sys_log = SystemLog(
                action='scan_correct',
                details=f"Team '{team.team_name}' scanned Clue {qr.clue_number} correctly.",
                team_id=team.id,
                ip_address=ip,
                browser=ua
            )
            db.session.add(sys_log)
            
        db.session.commit()

        # Emit SocketIO event
        socketio = current_app.extensions.get('socketio')
        if socketio:
            socketio.emit('qr_scanned', {
                'team_name': team.team_name,
                'house': team.house.name,
                'type': 'correct',
                'timestamp': timestamp.strftime('%Y-%m-%d %H:%M:%S'),
                'clue_number': qr.clue_number,
                'is_final': is_final
            })
            if is_final:
                socketio.emit('team_finished', {
                    'team_id': team.id,
                    'team_name': team.team_name,
                    'house': team.house.name,
                    'timestamp': timestamp.strftime('%Y-%m-%d %H:%M:%S')
                })

        if is_final:
            return {
                'status': 'completed_hunt',
                'message': 'Congratulations! You found all 7 clues. Please report to your House Manager.',
                'clue_number': 7,
                'password': qr.password,
                'hint': qr.hint,
                'image': qr.image_path
            }
        else:
            return {
                'status': 'success',
                'message': f"Clue {qr.clue_number} solved! Check details for your next destination.",
                'clue_number': qr.clue_number,
                'password': qr.password,
                'hint': qr.hint,
                'image': qr.image_path
            }
            
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error processing scan: {str(e)}")
        return {'status': 'error', 'message': 'An error occurred while saving scan progress.'}
