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
    next_clue_hint = "No active clues."
    current_clue_passcode = "N/A"
    current_clue = None
    if team.current_round == 2 and team.round1_status == 'approved':
        if team.round2_current_clue <= 7:
            # Query the clue level dynamically based on level and house restriction
            active_qrs = QRCode.query.filter_by(clue_number=team.round2_current_clue, is_dummy=False).all()
            for aq in active_qrs:
                if not aq.allowed_houses:
                    current_clue = aq
                    break
                allowed_list = [h.strip().lower() for h in aq.allowed_houses.split(',')]
                if team.house.name.lower() in allowed_list:
                    current_clue = aq
                    break

            if current_clue:
                next_clue_hint = current_clue.hint
                current_clue_passcode = current_clue.password

    # Pop preloaded scan result from session
    from flask import session as flask_session
    preloaded_scan_result = flask_session.pop('direct_scan_result', None)

    # Get latest scan log
    latest_scan = QRScanLog.query.filter_by(team_id=team.id).order_by(QRScanLog.timestamp.desc()).first()

    # Progress map for timeline display
    progress_map = {p.clue_number: p.completed_at for p in team.round2_progresses}

    # Build passcode map for solved clues
    passcode_map = {}
    all_qrs = QRCode.query.filter_by(is_dummy=False).all()
    for q in all_qrs:
        if not q.allowed_houses:
            passcode_map[q.clue_number] = q.password
        else:
            allowed_list = [h.strip().lower() for h in q.allowed_houses.split(',')]
            if team.house.name.lower() in allowed_list:
                passcode_map[q.clue_number] = q.password

    # Pass ISO timestamps for client stopwatch
    r1_app = Round1Approval.query.filter_by(team_id=team.id).first()
    approved_iso = r1_app.approved_at.isoformat() + "Z" if (r1_app and r1_app.approved_at) else ""
    created_iso = team.created_at.isoformat() + "Z" if team.created_at else ""
    finished_iso = team.round2_completion_time.isoformat() + "Z" if team.round2_completion_time else ""
    server_now_iso = datetime.datetime.utcnow().isoformat() + "Z"

    solved_count = 7 if team.round2_completed else (team.round2_current_clue - 1 if team.current_round == 2 else 0)
    score = (100 if team.round1_status == 'approved' else 0) + solved_count * 10
    
    clue_completion_times = {}
    clue_durations = {}
    
    r2_start = r1_app.approved_at if (r1_app and r1_app.approved_at) else team.created_at
    progresses = Round2Progress.query.filter_by(team_id=team.id).order_by(Round2Progress.clue_number).all()
    prog_map = {p.clue_number: p.completed_at for p in progresses}
    
    for lvl in range(1, 8):
        if lvl in prog_map:
            clue_completion_times[lvl] = prog_map[lvl]
            if lvl == 1:
                dur_sec = int((prog_map[1] - r2_start).total_seconds())
            else:
                prev_time = prog_map.get(lvl - 1)
                if prev_time:
                    dur_sec = int((prog_map[lvl] - prev_time).total_seconds())
                else:
                    dur_sec = 0
            m = dur_sec // 60
            s = dur_sec % 60
            clue_durations[lvl] = f"{m}m {s}s"

    return render_template('team/dashboard.html', 
                           team=team, 
                           tasks=tasks, 
                           completions=completions, 
                           completed_tasks_count=completed_tasks_count,
                           r1_progress_percent=r1_progress_percent,
                           current_clue=current_clue,
                           next_clue_hint=next_clue_hint,
                           current_clue_passcode=current_clue_passcode,
                           latest_scan=latest_scan,
                           created_iso=created_iso,
                           approved_iso=approved_iso,
                           finished_iso=finished_iso,
                           server_now_iso=server_now_iso,
                           progress_map=progress_map,
                           passcode_map=passcode_map,
                           preloaded_scan_result=preloaded_scan_result,
                           score=score,
                           clue_completion_times=clue_completion_times,
                           clue_durations=clue_durations)

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

@team_bp.route('/scan/<uuid>', methods=['GET', 'POST'])
def direct_scan(uuid):
    """Direct route for standard QR code reader apps scanning a URL."""
    if not current_user.is_authenticated or current_user.role != 'team_leader':
        # Store scanned code in session so they can submit after logging in
        from flask import session as flask_session
        flask_session['pending_scan_token'] = uuid
        flash("Scan detected! Please log in as your Team Leader to register the clue.", "info")
        return redirect(url_for('auth.login'))

    team = current_user.team_profile
    if not team:
        flash("Logged in user does not represent a Team.", "danger")
        return redirect(url_for('auth.logout'))

    qr = QRCode.query.filter_by(uuid=uuid).first()
    if not qr:
        flash("Invalid QR Code scanned!", "danger")
        return redirect(url_for('team.dashboard'))

    if request.method == 'POST':
        passcode = request.form.get('passcode', '').strip()
        result = process_qr_scan(team, uuid, passcode, request)
        
        if result['status'] == 'success':
            from flask import session as flask_session
            flask_session['direct_scan_result'] = result
            flash(f"Success: {result['message']}", "success")
            return redirect(url_for('team.dashboard'))
        elif result['status'] == 'completed_hunt':
            flash(f"CONGRATULATIONS: {result['message']}", "success")
            return redirect(url_for('team.dashboard'))
        elif result['status'] == 'dummy':
            from flask import session as flask_session
            flask_session['direct_scan_result'] = result
            flash(f"DUMMY CLUE: {result['message']}", "warning")
            return redirect(url_for('team.dashboard'))
        elif result['status'] == 'repeated':
            from flask import session as flask_session
            flask_session['direct_scan_result'] = result
            flash(f"INFO: {result['message']}", "info")
            return redirect(url_for('team.dashboard'))
        elif result['status'] == 'wrong_password':
            flash(f"Error: {result['message']}", "danger")
            return render_template('team/scan_verify.html', uuid=uuid)
        else:
            flash(f"Error: {result['message']}", "danger")
            return redirect(url_for('team.dashboard'))

    return render_template('team/scan_verify.html', uuid=uuid)

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
    passcode = data.get('passcode', '').strip()
    
    if not token:
        return jsonify({'status': 'error', 'message': 'No QR code token provided.'}), 400

    result = process_qr_scan(team, token, passcode, request)
    return jsonify(result)

def process_qr_scan(team, token, passcode, req):
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

    # 2.5. Check if clue is restricted to specific houses
    if qr.allowed_houses:
        allowed_list = [h.strip().lower() for h in qr.allowed_houses.split(',')]
        if team.house.name.lower() not in allowed_list:
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
                action='scan_failed_house_restriction',
                details=f"Team '{team.team_name}' scanned house-restricted Clue {qr.clue_number} (Allowed: {qr.allowed_houses}) which doesn't match their house.",
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
                    'type': 'wrong_house',
                    'timestamp': timestamp.strftime('%Y-%m-%d %H:%M:%S')
                })
                
            return {'status': 'wrong_house', 'message': 'Wrong Clue! This station belongs to another house.'}

    # Determine the required passcode for this station.
    # Level 1 requires no passcode (it is immediately available).
    # Level > 1 requires the password of the previous level clue (Clue X-1).
    if qr.clue_number == 1 and not qr.is_dummy:
        required_password = ""
    else:
        required_password = qr.password
        if qr.clue_number > 1 and not qr.is_dummy:
            prev_qrs = QRCode.query.filter_by(clue_number=qr.clue_number - 1, is_dummy=False).all()
            for pq in prev_qrs:
                if not pq.allowed_houses:
                    required_password = pq.password
                    break
                allowed_list = [h.strip().lower() for h in pq.allowed_houses.split(',')]
                if team.house.name.lower() in allowed_list:
                    required_password = pq.password
                    break

    # 3. Verify passcode (Case-insensitive check for user convenience)
    # If required_password is empty, it does not require a passcode.
    if required_password != "" and (not passcode or required_password.strip().lower() != passcode.strip().lower()):
        # Log wrong password scan
        log = QRScanLog(
            team_id=team.id,
            qr_code_id=qr.id,
            scanned_token=token,
            timestamp=timestamp,
            is_correct=False,
            is_repeated=False,
            is_dummy=qr.is_dummy,
            ip_address=ip,
            browser=ua
        )
        db.session.add(log)
        
        sys_log = SystemLog(
            action='scan_failed_passcode',
            details=f"Team '{team.team_name}' entered incorrect passcode '{passcode}' for Clue {qr.clue_number if not qr.is_dummy else 'Dummy'}.",
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
                'type': 'failed_passcode',
                'timestamp': timestamp.strftime('%Y-%m-%d %H:%M:%S')
            })
            
        return {'status': 'wrong_password', 'message': 'Incorrect passcode for this clue station.'}

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
        
        # Check if they have completed the Hunt (Clue 6 is the final scanned level)
        is_final = (qr.clue_number == 6)
        if is_final:
            team.round2_completed = True
            team.round2_completion_time = timestamp
            
            sys_log = SystemLog(
                action='hunt_completed',
                details=f"Team '{team.team_name}' successfully completed all 7 levels and finished the Hunt!",
                team_id=team.id,
                ip_address=ip,
                browser=ua
            )
            db.session.add(sys_log)
        else:
            sys_log = SystemLog(
                action='scan_correct',
                details=f"Team '{team.team_name}' scanned Level {qr.clue_number} correctly.",
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
                'message': 'Congratulations! You completed all 7 levels of the hunt. Please report to your House Manager.',
                'clue_number': 7,
                'password': 'N/A',
                'hint': 'None. You completed the Hunt!',
                'image': qr.image_path
            }
        else:
            # Query the next clue dynamically based on level and house restriction
            next_qrs = QRCode.query.filter_by(clue_number=qr.clue_number + 1, is_dummy=False).all()
            next_qr = None
            for nq in next_qrs:
                if not nq.allowed_houses:
                    next_qr = nq
                    break
                allowed_list = [h.strip().lower() for h in nq.allowed_houses.split(',')]
                if team.house.name.lower() in allowed_list:
                    next_qr = nq
                    break
                    
            next_pwd = next_qr.password if next_qr else 'N/A'
            return {
                'status': 'success',
                'message': f"Level {qr.clue_number} solved! Check details for your next destination.",
                'clue_number': qr.clue_number,
                'password': next_pwd,
                'hint': qr.hint,
                'image': qr.image_path
            }
            
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error processing scan: {str(e)}")
        return {'status': 'error', 'message': 'An error occurred while saving scan progress.'}

CLUE_FILES = {
    1: {
        'Red': 'Red 1.jpg',
        'Green': 'Green 1.jpg',
        'Blue': 'BLUE 1.jpg',
        'Yellow': 'YELLOW 1.jpg'
    },
    2: {
        'Red': 'Red 2.jpg',
        'Green': 'Green 2.jpg',
        'Blue': 'BLUE 2.jpg',
        'Yellow': 'YELLOW 2.jpg'
    },
    3: {
        'Red': 'PURPLE.jpg',
        'Blue': 'PURPLE.jpg',
        'Green': 'ORANGE.jpg',
        'Yellow': 'ORANGE.jpg'
    },
    4: {
        'Red': 'BLACK 1.jpg',
        'Green': 'BLACK 1.jpg',
        'Blue': 'BLACK 1.jpg',
        'Yellow': 'BLACK 1.jpg'
    },
    5: {
        'Red': 'BLACK 2.jpg',
        'Green': 'BLACK 2.jpg',
        'Blue': 'BLACK 2.jpg',
        'Yellow': 'BLACK 2.jpg'
    },
    6: {
        'Red': 'BLACK 3.jpg',
        'Green': 'BLACK 3.jpg',
        'Blue': 'BLACK 3.jpg',
        'Yellow': 'BLACK 3.jpg'
    },
    7: {
        'Red': 'BLACK FINAL.jpg',
        'Green': 'BLACK FINAL.jpg',
        'Blue': 'BLACK FINAL.jpg',
        'Yellow': 'BLACK FINAL.jpg'
    }
}

@team_bp.route('/team/clue-image/<int:level>')
@login_required
def serve_clue_image_by_level(level):
    from flask import send_from_directory, abort, redirect
    import os
    team = current_user.team_profile
    if not team:
        abort(403)
        
    # Allow viewing solved clues but restrict the active clue level and future locked clues
    if level >= team.round2_current_clue and not team.round2_completed:
        abort(403)
        
    # Query database for the clue matching the level and house restriction
    clue = None
    qrs = QRCode.query.filter_by(clue_number=level, is_dummy=False).all()
    for q in qrs:
        if not q.allowed_houses:
            clue = q
            break
        allowed_list = [h.strip().lower() for h in q.allowed_houses.split(',')]
        if team.house.name.lower() in allowed_list:
            clue = q
            break
            
    if not clue:
        abort(404)
        
    # If the database has an image path
    if clue.image_path:
        # Check if it's a static upload path (starts with /static/)
        if clue.image_path.startswith('/static/'):
            return redirect(clue.image_path)
            
        # Check if it's a relative/absolute file path or just a filename
        filename = clue.image_path
        clues_dir = r'C:\Users\ASUS\Downloads\clues'
        # Fallback to upload directory if not in clues_dir
        if not os.path.exists(os.path.join(clues_dir, filename)):
            upload_dir = current_app.config.get('UPLOAD_FOLDER', 'static/uploads')
            if os.path.exists(os.path.join(upload_dir, filename)):
                return send_from_directory(upload_dir, filename)
        return send_from_directory(clues_dir, filename)
        
    # Otherwise fallback to CLUE_FILES mapping
    house_name = team.house.name
    filename = None
    if level in CLUE_FILES:
        filename = CLUE_FILES[level].get(house_name)
        
    if not filename:
        abort(404)
        
    clues_dir = r'C:\Users\ASUS\Downloads\clues'
    return send_from_directory(clues_dir, filename)

@team_bp.route('/team/clue-image/dummy')
@login_required
def serve_dummy_image():
    from flask import send_from_directory
    clues_dir = r'C:\Users\ASUS\Downloads\clues'
    return send_from_directory(clues_dir, 'DUMMY.jpg')

@team_bp.route('/api/status')
@login_required
def team_api_status():
    team = current_user.team_profile
    if not team:
        return jsonify({'error': 'Team not found'}), 404
    return jsonify({
        'round1_status': team.round1_status,
        'current_round': team.current_round,
        'round2_completed': team.round2_completed
    })
