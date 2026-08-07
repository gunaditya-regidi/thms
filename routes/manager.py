import datetime
from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify, current_app
from flask_login import login_required, current_user
from models import db, Team, Task, TaskCompletion, Round1Approval, Manager, House, SystemLog, QRScanLog, QRCode

manager_bp = Blueprint('manager', __name__)

def check_manager_role(func):
    """Decorator to ensure user is logged in as a House Manager."""
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != 'manager':
            flash("Unauthorized. Please log in as a House Manager.", "danger")
            return redirect(url_for('auth.login'))
        return func(*args, **kwargs)
    wrapper.__name__ = func.__name__
    return wrapper

@manager_bp.route('/manager')
@check_manager_role
def dashboard():
    manager = current_user.manager_profile
    if not manager:
        flash("Manager profile not found.", "danger")
        logout_user()
        return redirect(url_for('auth.login'))

    house = manager.house
    teams = Team.query.filter_by(house_id=house.id).all()
    team_ids = [t.id for t in teams]

    # Calculate house statistics
    total_teams = len(teams)
    r1_pending = Team.query.filter_by(house_id=house.id, round1_status='requested').count()
    r1_completed = Team.query.filter(Team.house_id == house.id, Team.round1_status.in_(['verified', 'approved'])).count()
    r2_started = Team.query.filter_by(house_id=house.id, current_round=2, round2_completed=False).count()
    r2_finished = Team.query.filter_by(house_id=house.id, round2_completed=True).count()
    
    # Live scan count for house teams
    live_scan_count = 0
    if team_ids:
        live_scan_count = QRScanLog.query.filter(QRScanLog.team_id.in_(team_ids)).count()

    # Get recent activity logs for house teams
    recent_activity = []
    if team_ids:
        recent_activity = SystemLog.query.filter(SystemLog.team_id.in_(team_ids))\
                                         .order_by(SystemLog.timestamp.desc())\
                                         .limit(15).all()

    # Retrieve all tasks for verification checkboxes
    tasks = Task.query.order_by(Task.task_number).all()
    
    first_clue = QRCode.query.filter(
        QRCode.clue_number == 1,
        QRCode.is_dummy == False,
        QRCode.allowed_houses.like(f"%{house.name}%")
    ).first()

    import datetime
    server_now_iso = datetime.datetime.utcnow().isoformat() + "Z"

    return render_template('manager/dashboard.html',
                           house=house,
                           total_teams=total_teams,
                           r1_pending=r1_pending,
                           r1_completed=r1_completed,
                           r2_started=r2_started,
                           r2_finished=r2_finished,
                           live_scan_count=live_scan_count,
                           teams=teams,
                           tasks=tasks,
                           recent_activity=recent_activity,
                           first_clue=first_clue,
                           server_now_iso=server_now_iso)

@manager_bp.route('/manager/verify-task/<int:team_id>/<int:task_id>', methods=['POST'])
@check_manager_role
def verify_task(team_id, task_id):
    manager = current_user.manager_profile
    team = Team.query.get_or_404(team_id)
    
    # Security: Ensure team belongs to manager's house
    if team.house_id != manager.house_id:
        return jsonify({'status': 'error', 'message': 'Unauthorized. Team belongs to another house.'}), 403

    # Check if task already completed
    existing = TaskCompletion.query.filter_by(team_id=team_id, task_id=task_id).first()
    
    data = request.get_json() or {}
    completed = data.get('completed', False)

    try:
        if completed:
            if not existing:
                tc = TaskCompletion(
                    team_id=team_id,
                    task_id=task_id,
                    completed_at=datetime.datetime.utcnow(),
                    verified_by_manager_id=manager.id
                )
                db.session.add(tc)
                
                # Audit log
                task = Task.query.get(task_id)
                log = SystemLog(
                    action='task_completed',
                    details=f"House Manager '{current_user.username}' marked Task {task.task_number} ('{task.title}') as Completed for Team '{team.team_name}'.",
                    user_id=current_user.id,
                    team_id=team_id,
                    ip_address=request.remote_addr,
                    browser=request.user_agent.string
                )
                db.session.add(log)
        else:
            if existing:
                # If they request completion, they shouldn't easily uncheck, but allow managers to revert mistakes in R1 active
                if team.round1_status in ['active', 'requested']:
                    db.session.delete(existing)
                    # Audit log
                    task = Task.query.get(task_id)
                    log = SystemLog(
                        action='task_uncompleted',
                        details=f"House Manager '{current_user.username}' removed Task {task.task_number} ('{task.title}') completion for Team '{team.team_name}'.",
                        user_id=current_user.id,
                        team_id=team_id,
                        ip_address=request.remote_addr,
                        browser=request.user_agent.string
                    )
                    db.session.add(log)
                else:
                    return jsonify({'status': 'error', 'message': 'Cannot modify task completion. Round 1 approved.'}), 400

        db.session.commit()

        # Emit SocketIO event
        socketio = current_app.extensions.get('socketio')
        if socketio:
            socketio.emit('task_completed', {
                'team_id': team_id,
                'team_name': team.team_name,
                'house': manager.house.name,
                'completed_count': TaskCompletion.query.filter_by(team_id=team_id).count()
            })

        return jsonify({'status': 'success', 'message': 'Task status updated.'})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error verifying task: {str(e)}")
        return jsonify({'status': 'error', 'message': 'Database error occurred.'}), 500

@manager_bp.route('/manager/verify-r1/<int:team_id>', methods=['POST'])
@check_manager_role
def verify_r1(team_id):
    manager = current_user.manager_profile
    team = Team.query.get_or_404(team_id)

    # Security: Ensure team belongs to manager's house
    if team.house_id != manager.house_id:
        flash("Unauthorized. Team belongs to another house.", "danger")
        return redirect(url_for('manager.dashboard'))

    if team.round1_status != 'requested':
        flash("Team has not requested Round 1 verification yet or it is already processed.", "warning")
        return redirect(url_for('manager.dashboard'))

    try:
        team.round1_status = 'verified'
        
        # Load or create approval record
        approval = Round1Approval.query.filter_by(team_id=team_id).first()
        if not approval:
            approval = Round1Approval(team_id=team_id)
            db.session.add(approval)
            
        approval.verified_at = datetime.datetime.utcnow()
        approval.verified_by_manager_id = manager.id

        # Audit log
        log = SystemLog(
            action='r1_verified',
            details=f"House Manager '{current_user.username}' physically verified and approved Round 1 for Team '{team.team_name}'.",
            user_id=current_user.id,
            team_id=team_id,
            ip_address=request.remote_addr,
            browser=request.user_agent.string
        )
        db.session.add(log)
        db.session.commit()

        # Emit SocketIO event
        socketio = current_app.extensions.get('socketio')
        if socketio:
            socketio.emit('r1_verified', {
                'team_id': team_id,
                'team_name': team.team_name,
                'house': manager.house.name
            })

        flash(f"Round 1 verification completed for Team {team.team_name}. Sent to Admin for final approval.", "success")
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error in verifying R1 completion: {str(e)}")
        flash("An error occurred. Please try again.", "danger")

    return redirect(url_for('manager.dashboard'))

@manager_bp.route('/manager/verify-all-tasks/<int:team_id>', methods=['POST'])
@check_manager_role
def verify_all_tasks(team_id):
    manager = current_user.manager_profile
    team = Team.query.get_or_404(team_id)
    
    # Security: Ensure team belongs to manager's house
    if team.house_id != manager.house_id:
        return jsonify({'status': 'error', 'message': 'Unauthorized. Team belongs to another house.'}), 403

    if team.round1_status in ['verified', 'approved']:
        return jsonify({'status': 'error', 'message': 'Cannot modify task completion. Round 1 already verified/approved.'}), 400

    try:
        tasks = Task.query.all()
        now = datetime.datetime.utcnow()
        for task in tasks:
            existing = TaskCompletion.query.filter_by(team_id=team_id, task_id=task.id).first()
            if not existing:
                tc = TaskCompletion(
                    team_id=team_id,
                    task_id=task.id,
                    completed_at=now,
                    verified_by_manager_id=manager.id
                )
                db.session.add(tc)

        # Audit log
        log = SystemLog(
            action='all_tasks_completed',
            details=f"House Manager '{current_user.username}' marked ALL Tasks as Completed for Team '{team.team_name}'.",
            user_id=current_user.id,
            team_id=team_id,
            ip_address=request.remote_addr,
            browser=request.user_agent.string
        )
        db.session.add(log)
        db.session.commit()

        # Emit SocketIO event
        socketio = current_app.extensions.get('socketio')
        if socketio:
            socketio.emit('task_completed', {
                'team_id': team_id,
                'team_name': team.team_name,
                'house': manager.house.name,
                'completed_count': 10
            })

        return jsonify({'status': 'success', 'message': 'All tasks marked as completed.'})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error verifying all tasks: {str(e)}")
        return jsonify({'status': 'error', 'message': 'Database error occurred.'}), 500

