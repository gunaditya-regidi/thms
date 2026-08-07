import os
import csv
import io
import uuid
import zipfile
from datetime import datetime
from flask import Blueprint, render_template, redirect, url_for, flash, request, send_file, current_app, jsonify
from flask_login import login_required, current_user
from models import db, User, Team, Member, House, Task, TaskCompletion, Round1Approval, QRCode, Round2Progress, QRScanLog, SystemLog, Manager
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash

# ReportLab imports for PDF generation
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, Image
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch

# OpenPyXL imports for Excel generation
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

admin_bp = Blueprint('admin', __name__)

def check_admin_role(func):
    """Decorator to ensure user is logged in as Admin."""
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != 'admin':
            flash("Unauthorized. Please log in as an Administrator.", "danger")
            return redirect(url_for('auth.login'))
        return func(*args, **kwargs)
    wrapper.__name__ = func.__name__
    return wrapper

def generate_qr_image(uuid_str):
    """Generates a QR code image pointing to the direct scan URL."""
    import qrcode
    qr_dir = current_app.config['QR_FOLDER']
    os.makedirs(qr_dir, exist_ok=True)
    
    # We construct the URL dynamically
    scan_url = url_for('team.direct_scan', uuid=uuid_str, _external=True)
    
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(scan_url)
    qr.make(fit=True)
    
    img = qr.make_image(fill_color="black", back_color="white")
    filename = f"qr_{uuid_str}.png"
    filepath = os.path.join(qr_dir, filename)
    img.save(filepath)
    return filename

@admin_bp.route('/admin')
@check_admin_role
def dashboard():
    # Gather general statistics
    total_teams = Team.query.count()
    total_managers = Manager.query.count()
    total_tasks = Task.query.count()
    total_qrs = QRCode.query.count()
    
    # Houses summary
    houses = House.query.all()
    
    # Round progress counts
    r1_pending_start = Team.query.filter_by(round1_status='pending_start').count()
    r1_active = Team.query.filter_by(current_round=1, round1_status='active').count()
    r1_pending_approval = Team.query.filter_by(round1_status='requested').count()
    r1_verified = Team.query.filter_by(round1_status='verified').count()
    r2_active = Team.query.filter_by(current_round=2, round2_completed=False).count()
    r2_completed = Team.query.filter_by(round2_completed=True).count()
    
    # Fetch recent registrations and scan activity
    recent_teams = Team.query.order_by(Team.created_at.desc()).limit(5).all()
    recent_scans = QRScanLog.query.order_by(QRScanLog.timestamp.desc()).limit(5).all()

    users = User.query.all()
    return render_template('admin/dashboard.html',
                           total_teams=total_teams,
                           total_managers=total_managers,
                           total_tasks=total_tasks,
                           total_qrs=total_qrs,
                           houses=houses,
                           r1_pending_start=r1_pending_start,
                           r1_active=r1_active,
                           r1_pending_approval=r1_pending_approval,
                           r1_verified=r1_verified,
                           r2_active=r2_active,
                           r2_completed=r2_completed,
                           recent_teams=recent_teams,
                           recent_scans=recent_scans,
                           users=users)

@admin_bp.route('/admin/teams')
@check_admin_role
def teams_list():
    teams = Team.query.order_by(Team.created_at.desc()).all()
    return render_template('admin/teams.html', teams=teams)

@admin_bp.route('/admin/approve-r1/<int:team_id>', methods=['POST'])
@check_admin_role
def approve_r1(team_id):
    team = Team.query.get_or_404(team_id)
    if team.round1_status not in ['requested', 'verified']:
        flash("Round 1 completion has not been requested or is already approved.", "warning")
        return redirect(url_for('admin.teams_list'))

    try:
        team.round1_status = 'approved'
        team.current_round = 2 # Automatically unlocks Round 2!
        
        # Load or create approval record
        approval = Round1Approval.query.filter_by(team_id=team_id).first()
        if not approval:
            approval = Round1Approval(team_id=team_id)
            db.session.add(approval)
            
        approval.approved_at = datetime.utcnow()
        approval.approved_by_admin_id = current_user.id

        # Audit log
        log = SystemLog(
            action='r1_approved',
            details=f"Admin '{current_user.username}' approved Round 1 for Team '{team.team_name}'. Official completion timestamp saved.",
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
            socketio.emit('r1_approved', {
                'team_id': team_id,
                'team_name': team.team_name,
                'house': team.house.name,
                'timestamp': approval.approved_at.strftime('%Y-%m-%d %H:%M:%S')
            })

        flash(f"Round 1 approved and Round 2 unlocked for Team {team.team_name}.", "success")
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error in approving R1 completion: {str(e)}")
        flash("An error occurred. Please try again.", "danger")

    return redirect(url_for('admin.teams_list'))

@admin_bp.route('/admin/approve-start/<int:team_id>', methods=['POST'])
@check_admin_role
def approve_start(team_id):
    team = Team.query.get_or_404(team_id)
    if team.round1_status != 'pending_start':
        flash("Team is already started or past starting phase.", "warning")
        return redirect(url_for('admin.teams_list'))

    try:
        from datetime import datetime
        now = datetime.utcnow()
        team.round1_status = 'active'
        team.created_at = now
        
        log = SystemLog(
            action='r1_started',
            details=f"Admin '{current_user.username}' approved Team '{team.team_name}' to start Round 1.",
            user_id=current_user.id,
            team_id=team_id,
            ip_address=request.remote_addr,
            browser=request.user_agent.string
        )
        db.session.add(log)
        db.session.commit()

        socketio = current_app.extensions.get('socketio')
        if socketio:
            socketio.emit('team_started', {
                'team_id': team_id,
                'team_name': team.team_name,
                'house': team.house.name,
                'timestamp': now.strftime('%Y-%m-%d %H:%M:%S')
            })

        flash(f"Team {team.team_name} approved to start Round 1.", "success")
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error in starting team: {str(e)}")
        flash("An error occurred. Please try again.", "danger")

    return redirect(url_for('admin.teams_list'))

@admin_bp.route('/admin/approve-start-all', methods=['POST'])
@check_admin_role
def approve_start_all():
    pending_teams = Team.query.filter_by(round1_status='pending_start').all()
    if not pending_teams:
        flash("No teams are currently pending approval to start.", "warning")
        return redirect(url_for('admin.dashboard'))

    try:
        from datetime import datetime
        now = datetime.utcnow()
        socketio = current_app.extensions.get('socketio')
        
        count = 0
        for team in pending_teams:
            team.round1_status = 'active'
            team.created_at = now
            
            log = SystemLog(
                action='r1_started',
                details=f"Admin '{current_user.username}' approved Team '{team.team_name}' to start Round 1 via universal start.",
                user_id=current_user.id,
                team_id=team.id,
                ip_address=request.remote_addr,
                browser=request.user_agent.string
            )
            db.session.add(log)
            count += 1
            
            if socketio:
                socketio.emit('team_started', {
                    'team_id': team.id,
                    'team_name': team.team_name,
                    'house': team.house.name,
                    'timestamp': now.strftime('%Y-%m-%d %H:%M:%S')
                })

        db.session.commit()
        flash(f"Successfully approved and started {count} teams simultaneously!", "success")
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error in starting all teams: {str(e)}")
        flash("An error occurred while starting all teams.", "danger")

    return redirect(url_for('admin.dashboard'))

@admin_bp.route('/admin/promote-r2/<int:team_id>', methods=['POST'])
@check_admin_role
def promote_r2(team_id):
    team = Team.query.get_or_404(team_id)
    try:
        # Complete all 10 tasks if they aren't complete
        tasks = Task.query.all()
        now = datetime.utcnow()
        for task in tasks:
            existing = TaskCompletion.query.filter_by(team_id=team.id, task_id=task.id).first()
            if not existing:
                house_manager = Manager.query.filter_by(house_id=team.house_id).first()
                manager_id = house_manager.id if house_manager else 1
                
                tc = TaskCompletion(
                    team_id=team.id,
                    task_id=task.id,
                    completed_at=now,
                    verified_by_manager_id=manager_id
                )
                db.session.add(tc)

        # Set status approved and round to 2
        team.round1_status = 'approved'
        team.current_round = 2
        
        # Build approval record
        approval = Round1Approval.query.filter_by(team_id=team.id).first()
        if not approval:
            approval = Round1Approval(team_id=team.id)
            db.session.add(approval)
        
        if not approval.requested_at:
            approval.requested_at = now
        if not approval.verified_at:
            approval.verified_at = now
        approval.approved_at = now
        approval.approved_by_admin_id = current_user.id
        
        # Audit log
        log = SystemLog(
            action='team_promoted_r2',
            details=f"Admin '{current_user.username}' directly PROMOTED Team '{team.team_name}' to Round 2.",
            user_id=current_user.id,
            team_id=team.id,
            ip_address=request.remote_addr,
            browser=request.user_agent.string
        )
        db.session.add(log)
        db.session.commit()

        # Emit SocketIO event
        socketio = current_app.extensions.get('socketio')
        if socketio:
            socketio.emit('r1_approved', {
                'team_id': team.id,
                'team_name': team.team_name,
                'house': team.house.name,
                'timestamp': now.strftime('%Y-%m-%d %H:%M:%S')
            })

        flash(f"Team {team.team_name} successfully promoted directly to Round 2.", "success")
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error promoting team: {str(e)}")
        flash("An error occurred during promotion.", "danger")

    return redirect(url_for('admin.teams_list'))


@admin_bp.route('/admin/reset/<int:team_id>', methods=['POST'])
@check_admin_role
def reset_team(team_id):
    team = Team.query.get_or_404(team_id)
    try:
        # Clear completions, approvals, and round 2 progress
        TaskCompletion.query.filter_by(team_id=team_id).delete()
        Round1Approval.query.filter_by(team_id=team_id).delete()
        Round2Progress.query.filter_by(team_id=team_id).delete()
        
        # Reset team attributes
        team.current_round = 1
        team.round1_status = 'pending_start'
        team.round2_current_clue = 1
        team.round2_completed = False
        team.round2_completion_time = None
        
        # Audit log
        log = SystemLog(
            action='team_reset',
            details=f"Admin '{current_user.username}' reset all progress (R1 & R2) for Team '{team.team_name}'.",
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
            socketio.emit('team_reset', {
                'team_id': team_id,
                'team_name': team.team_name
            })

        flash(f"Team {team.team_name} progress has been completely reset.", "success")
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error resetting team: {str(e)}")
        flash("An error occurred during reset.", "danger")

    return redirect(url_for('admin.teams_list'))

@admin_bp.route('/admin/reset-qr/<int:team_id>', methods=['POST'])
@check_admin_role
def reset_qr_progress(team_id):
    team = Team.query.get_or_404(team_id)
    try:
        # Clear round 2 progress only
        Round2Progress.query.filter_by(team_id=team_id).delete()
        
        team.round2_current_clue = 1
        team.round2_completed = False
        team.round2_completion_time = None
        
        # Audit log
        log = SystemLog(
            action='team_qr_reset',
            details=f"Admin '{current_user.username}' reset Round 2 QR clue progress for Team '{team.team_name}'.",
            user_id=current_user.id,
            team_id=team_id,
            ip_address=request.remote_addr,
            browser=request.user_agent.string
        )
        db.session.add(log)
        db.session.commit()

        # Emit SocketIO
        socketio = current_app.extensions.get('socketio')
        if socketio:
            socketio.emit('team_qr_reset', {
                'team_id': team_id,
                'team_name': team.team_name
            })

        flash(f"Round 2 QR progress has been reset for Team {team.team_name}.", "success")
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error resetting QR progress: {str(e)}")
        flash("An error occurred.", "danger")

    return redirect(url_for('admin.teams_list'))

@admin_bp.route('/admin/reset-all', methods=['POST'])
@check_admin_role
def reset_all_teams():
    try:
        # Delete task completions, approvals, progresses, and scan logs
        TaskCompletion.query.delete()
        Round1Approval.query.delete()
        Round2Progress.query.delete()
        QRScanLog.query.delete()
        
        # Reset all team status values
        teams = Team.query.all()
        for team in teams:
            team.current_round = 1
            team.round1_status = 'pending_start'
            team.round2_current_clue = 1
            team.round2_completed = False
            team.round2_completion_time = None
            
        # Log system event
        sys_log = SystemLog(
            action='system_reset_all',
            details=f"Admin '{current_user.username}' reset ALL teams and clue progress to zero.",
            user_id=current_user.id,
            ip_address=request.remote_addr,
            browser=request.user_agent.string
        )
        db.session.add(sys_log)
        db.session.commit()
        
        # Emit SocketIO global reset signal so all dashboards reload/update
        socketio = current_app.extensions.get('socketio')
        if socketio:
            socketio.emit('global_reset', {})
            
        flash("All teams, tasks, and clue progress have been completely reset to zero.", "success")
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error executing global reset: {str(e)}")
        flash("An error occurred during global reset.", "danger")
        
    return redirect(url_for('admin.dashboard'))

@admin_bp.route('/admin/qrs', methods=['GET', 'POST'])
@check_admin_role
def manage_qrs():
    if request.method == 'POST':
        clue_number = int(request.form.get('clue_number', 0))
        password = request.form.get('password', '').strip()
        hint = request.form.get('hint', '').strip()
        is_dummy = request.form.get('is_dummy') == 'true'
        
        if not password or not hint:
            flash("Password and Hint are required.", "danger")
            return redirect(url_for('admin.manage_qrs'))

        try:
            # Handle image upload
            image_path = None
            if 'image' in request.files:
                file = request.files['image']
                if file and file.filename != '':
                    filename = secure_filename(f"clue_{clue_number}_{uuid.uuid4().hex[:6]}_{file.filename}")
                    upload_dir = current_app.config['UPLOAD_FOLDER']
                    os.makedirs(upload_dir, exist_ok=True)
                    file_save_path = os.path.join(upload_dir, filename)
                    file.save(file_save_path)
                    image_path = f"/static/uploads/{filename}"

            # Create code record
            code_uuid = str(uuid.uuid4())
            new_qr = QRCode(
                uuid=code_uuid,
                clue_number=clue_number if not is_dummy else 99,
                password=password,
                hint=hint,
                image_path=image_path,
                is_dummy=is_dummy
            )
            db.session.add(new_qr)
            db.session.flush() # gets ID for filename creation
            
            # Generate QR code PNG
            generate_qr_image(code_uuid)

            # System log
            log = SystemLog(
                action='qr_generated',
                details=f"Admin '{current_user.username}' generated {'Dummy ' if is_dummy else ''}QR Clue {clue_number} (UUID: {code_uuid}).",
                user_id=current_user.id
            )
            db.session.add(log)
            db.session.commit()

            flash("QR Code generated successfully!", "success")
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error generating QR code: {str(e)}")
            flash("An error occurred while generating QR Code.", "danger")
            
        return redirect(url_for('admin.manage_qrs'))

    qrs = QRCode.query.order_by(QRCode.is_dummy, QRCode.clue_number).all()
    return render_template('admin/qrs.html', qrs=qrs)

@admin_bp.route('/admin/qrs/delete/<int:qr_id>', methods=['POST'])
@check_admin_role
def delete_qr(qr_id):
    qr = QRCode.query.get_or_404(qr_id)
    try:
        # Delete file from directory if exists
        filename = f"qr_{qr.uuid}.png"
        filepath = os.path.join(current_app.config['QR_FOLDER'], filename)
        if os.path.exists(filepath):
            os.remove(filepath)
            
        # Delete clue image if uploaded
        if qr.image_path:
            img_filename = qr.image_path.split('/')[-1]
            img_filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], img_filename)
            if os.path.exists(img_filepath):
                os.remove(img_filepath)

        db.session.delete(qr)
        
        # Log action
        log = SystemLog(
            action='qr_deleted',
            details=f"Admin '{current_user.username}' deleted QR Clue {qr.clue_number} (UUID: {qr.uuid}).",
            user_id=current_user.id
        )
        db.session.add(log)
        db.session.commit()
        
        flash("QR Code deleted successfully.", "success")
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error deleting QR: {str(e)}")
        flash("An error occurred during deletion.", "danger")

    return redirect(url_for('admin.manage_qrs'))

@admin_bp.route('/admin/qrs/export-zip')
@check_admin_role
def export_qrs_zip():
    """Generates all QR code PNGs and exports them as a single ZIP archive."""
    qrs = QRCode.query.all()
    qr_dir = current_app.config['QR_FOLDER']
    
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w') as zip_file:
        for qr in qrs:
            filename = f"qr_{qr.uuid}.png"
            filepath = os.path.join(qr_dir, filename)
            
            # Re-generate if file missing on disk
            if not os.path.exists(filepath):
                generate_qr_image(qr.uuid)
                
            # Define label for ZIP
            label = f"Clue_{qr.clue_number}_{qr.uuid[:6]}.png" if not qr.is_dummy else f"Dummy_{qr.uuid[:6]}.png"
            zip_file.write(filepath, label)
            
    zip_buffer.seek(0)
    
    # Audit log
    log = SystemLog(action='export_qrs_zip', details="Admin exported all QR codes as ZIP archive.", user_id=current_user.id)
    db.session.add(log)
    db.session.commit()
    
    return send_file(
        zip_buffer,
        mimetype='application/zip',
        as_attachment=True,
        download_name='treasure_hunt_qrs.zip'
    )

@admin_bp.route('/admin/qrs/export-pdf')
@check_admin_role
def export_qrs_pdf():
    """Generates a printable PDF document with QR code sheets containing clue numbers, details, and QR codes."""
    qrs = QRCode.query.order_by(QRCode.is_dummy, QRCode.clue_number).all()
    qr_dir = current_app.config['QR_FOLDER']
    
    pdf_buffer = io.BytesIO()
    doc = SimpleDocTemplate(pdf_buffer, pagesize=letter, leftMargin=0.5*inch, rightMargin=0.5*inch, topMargin=0.5*inch, bottomMargin=0.5*inch)
    
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Heading1'],
        fontName='Helvetica-Bold',
        fontSize=48,
        leading=56,
        textColor=colors.HexColor('#000000'),
        alignment=1, # Center
        spaceAfter=0
    )
    
    subtitle_style = ParagraphStyle(
        'DocSubtitle',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=24,
        leading=28,
        textColor=colors.HexColor('#666666'),
        alignment=1, # Center
        spaceAfter=0
    )
    
    elements = []
    
    for idx, qr in enumerate(qrs):
        filename = f"qr_{qr.uuid}.png"
        filepath = os.path.join(qr_dir, filename)
        if not os.path.exists(filepath):
            generate_qr_image(qr.uuid)
            
        # Large heading style
        if qr.is_dummy:
            heading_text = "CLUE"
        elif qr.allowed_houses:
            heading_text = f"CLUE #{qr.clue_number} ({qr.allowed_houses.upper()})"
        else:
            heading_text = f"CLUE #{qr.clue_number}"
        
        elements.append(Paragraph(heading_text, title_style))
        elements.append(Spacer(1, 0.4*inch))
        
        # "Scan Here" instruction
        elements.append(Paragraph("SCAN HERE", subtitle_style))
        elements.append(Spacer(1, 0.5*inch))
        
        # Giant QR Code Image centered
        qr_image = Image(filepath, width=5.5*inch, height=5.5*inch)
        qr_table = Table([[qr_image]], colWidths=[7.5*inch])
        qr_table.setStyle(TableStyle([
            ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
            ('BOTTOMPADDING', (0,0), (-1,-1), 0),
            ('TOPPADDING', (0,0), (-1,-1), 0)
        ]))
        elements.append(qr_table)
        
        # Page break except for the last clue sheet
        if idx < len(qrs) - 1:
            elements.append(PageBreak())
            
    doc.build(elements)
    pdf_buffer.seek(0)
    
    # Audit log
    log = SystemLog(action='export_qrs_pdf', details="Admin exported all QR code clue sheets as printable PDF.", user_id=current_user.id)
    db.session.add(log)
    db.session.commit()
    
    return send_file(
        pdf_buffer,
        mimetype='application/pdf',
        as_attachment=True,
        download_name='treasure_hunt_qr_sheets.pdf'
    )

@admin_bp.route('/admin/logs')
@check_admin_role
def view_logs():
    page = request.args.get('page', 1, type=int)
    action_filter = request.args.get('action_filter', '').strip()
    search = request.args.get('search', '').strip()
    
    query = SystemLog.query
    
    if action_filter:
        query = query.filter_by(action=action_filter)
        
    if search:
        query = query.filter(
            (SystemLog.details.like(f"%{search}%")) |
            (SystemLog.action.like(f"%{search}%"))
        )
        
    pagination = query.order_by(SystemLog.timestamp.desc()).paginate(page=page, per_page=25)
    logs = pagination.items
    
    # Retrieve all unique actions for filtering dropdown
    distinct_actions = db.session.query(SystemLog.action).distinct().all()
    actions = [a[0] for a in distinct_actions]

    return render_template('admin/logs.html',
                           logs=logs,
                           pagination=pagination,
                           actions=actions,
                           current_action=action_filter,
                           search=search)

@admin_bp.route('/admin/managers', methods=['GET', 'POST'])
@check_admin_role
def manage_managers():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        house_id = int(request.form.get('house_id', 0))
        
        if not username or not password or not house_id:
            flash("All fields are required.", "danger")
            return redirect(url_for('admin.manage_managers'))
            
        if User.query.filter_by(username=username).first():
            flash(f"Username '{username}' is already in use.", "danger")
            return redirect(url_for('admin.manage_managers'))

        try:
            # Create base user
            user = User(username=username, role='manager')
            user.set_password(password)
            db.session.add(user)
            db.session.flush()
            
            # Create manager profile
            mgr = Manager(user_id=user.id, house_id=house_id)
            db.session.add(mgr)
            
            # Audit log
            log = SystemLog(
                action='manager_created',
                details=f"Admin '{current_user.username}' created Manager '{username}' for House ID {house_id}.",
                user_id=current_user.id
            )
            db.session.add(log)
            db.session.commit()
            
            flash(f"Manager '{username}' created successfully.", "success")
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error creating manager: {str(e)}")
            flash("An error occurred.", "danger")
            
        return redirect(url_for('admin.manage_managers'))

    managers = Manager.query.all()
    houses = House.query.all()
    return render_template('admin/managers.html', managers=managers, houses=houses)

@admin_bp.route('/admin/managers/delete/<int:mgr_id>', methods=['POST'])
@check_admin_role
def delete_manager(mgr_id):
    mgr = Manager.query.get_or_404(mgr_id)
    user = mgr.user
    
    # Restrict deletion of default seeded managers to prevent accidental lockouts
    seeded_managers = ['Aditya', 'Shyam', 'Rahul', 'Saranya']
    if user.username in seeded_managers:
        flash(f"Cannot delete default seeded manager '{user.username}' to maintain system safety.", "warning")
        return redirect(url_for('admin.manage_managers'))

    try:
        # Delete user (deletes manager profile via cascade)
        db.session.delete(user)
        
        # Log action
        log = SystemLog(
            action='manager_deleted',
            details=f"Admin '{current_user.username}' deleted Manager '{user.username}'.",
            user_id=current_user.id
        )
        db.session.add(log)
        db.session.commit()
        
        flash("Manager deleted successfully.", "success")
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error deleting manager: {str(e)}")
        flash("An error occurred during deletion.", "danger")

    return redirect(url_for('admin.manage_managers'))


# ==========================================
# REPORT EXPORT ENDPOINTS
# ==========================================

@admin_bp.route('/admin/reports/download')
@check_admin_role
def download_reports_dashboard():
    return render_template('admin/reports.html')

@admin_bp.route('/admin/reports/export/<report_type>/<format_type>')
@check_admin_role
def export_report(report_type, format_type):
    """
    Export reports:
    - report_type: 'house_summary', 'leaderboard', 'qr_analytics', 'team_progress'
    - format_type: 'csv', 'excel', 'pdf'
    """
    timestamp_str = datetime.now().strftime('%Y%m%d_%H%M%S')
    
    # 1. Gather Data
    headers, data = get_report_data(report_type)
    
    if not headers:
        flash("Invalid report type.", "danger")
        return redirect(url_for('admin.download_reports_dashboard'))
        
    filename = f"{report_type}_report_{timestamp_str}.{format_type}"

    # 2. Output Formatting
    if format_type == 'csv':
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(headers)
        writer.writerows(data)
        
        output.seek(0)
        
        log = SystemLog(action=f'export_{report_type}_csv', details=f"Admin exported {report_type} as CSV.", user_id=current_user.id)
        db.session.add(log)
        db.session.commit()
        
        return send_file(
            io.BytesIO(output.getvalue().encode('utf-8')),
            mimetype='text/csv',
            as_attachment=True,
            download_name=filename
        )
        
    elif format_type == 'excel':
        wb = Workbook()
        ws = wb.active
        ws.title = report_type.replace('_', ' ').title()
        
        # Styles
        header_fill = PatternFill(start_color='0d6efd', end_color='0d6efd', fill_type='solid')
        header_font = Font(name='Arial', size=11, bold=True, color='FFFFFF')
        data_font = Font(name='Arial', size=10)
        border_side = Side(style='thin', color='DDDDDD')
        border = Border(left=border_side, right=border_side, top=border_side, bottom=border_side)
        
        # Write headers
        ws.append(headers)
        for col_idx in range(1, len(headers) + 1):
            cell = ws.cell(row=1, column=col_idx)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal='center', vertical='center')
            cell.border = border
            
        # Write data
        for row in data:
            ws.append(row)
            
        # Style rows and auto-fit columns
        for row_idx in range(2, len(data) + 2):
            for col_idx in range(1, len(headers) + 1):
                cell = ws.cell(row=row_idx, column=col_idx)
                cell.font = data_font
                cell.border = border
                
        # Auto-adjust column width
        for col in ws.columns:
            max_len = max(len(str(cell.value or '')) for cell in col)
            col_letter = col[0].column_letter
            ws.column_dimensions[col_letter].width = max(max_len + 3, 10)
            
        ws.row_dimensions[1].height = 25
        
        excel_buffer = io.BytesIO()
        wb.save(excel_buffer)
        excel_buffer.seek(0)
        
        log = SystemLog(action=f'export_{report_type}_excel', details=f"Admin exported {report_type} as Excel.", user_id=current_user.id)
        db.session.add(log)
        db.session.commit()
        
        return send_file(
            excel_buffer,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=filename
        )
        
    elif format_type == 'pdf':
        pdf_buffer = io.BytesIO()
        doc = SimpleDocTemplate(pdf_buffer, pagesize=letter, leftMargin=0.5*inch, rightMargin=0.5*inch, topMargin=0.5*inch, bottomMargin=0.5*inch)
        
        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            'ReportTitle',
            parent=styles['Heading1'],
            fontName='Helvetica-Bold',
            fontSize=18,
            textColor=colors.HexColor('#0d6efd'),
            alignment=1,
            spaceAfter=15
        )
        
        elements = []
        elements.append(Paragraph(report_type.replace('_', ' ').upper() + " REPORT", title_style))
        elements.append(Paragraph(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", styles['Normal']))
        elements.append(Spacer(1, 0.2*inch))
        
        # Build Table
        table_content = [headers] + [[str(val) for val in row] for row in data]
        
        # Estimate column widths based on page width (7.5 inches printable)
        num_cols = len(headers)
        col_width = (7.5 * inch) / num_cols
        
        t = Table(table_content, colWidths=[col_width]*num_cols)
        t.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#0d6efd')),
            ('TEXTCOLOR', (0,0), (-1,0), colors.white),
            ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
            ('FONTSIZE', (0,0), (-1,0), 10),
            ('BOTTOMPADDING', (0,0), (-1,0), 8),
            ('TOPPADDING', (0,0), (-1,0), 8),
            ('BACKGROUND', (0,1), (-1,-1), colors.HexColor('#f8f9fa')),
            ('TEXTCOLOR', (0,1), (-1,-1), colors.black),
            ('FONTNAME', (0,1), (-1,-1), 'Helvetica'),
            ('FONTSIZE', (0,1), (-1,-1), 9),
            ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#dee2e6')),
            ('PADDING', (0,0), (-1,-1), 5),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE')
        ]))
        
        elements.append(t)
        doc.build(elements)
        pdf_buffer.seek(0)
        
        log = SystemLog(action=f'export_{report_type}_pdf', details=f"Admin exported {report_type} as PDF.", user_id=current_user.id)
        db.session.add(log)
        db.session.commit()
        
        return send_file(
            pdf_buffer,
            mimetype='application/pdf',
            as_attachment=True,
            download_name=filename
        )
        
    flash("Invalid format type.", "danger")
    return redirect(url_for('admin.download_reports_dashboard'))

def get_report_data(report_type):
    """Data helper mapping report queries to output lists."""
    if report_type == 'house_summary':
        headers = ['House Name', 'Total Teams', 'R1 Pending', 'R1 Completed', 'R2 Started', 'R2 Finished', 'Total QR Scans']
        data = []
        houses = House.query.all()
        for h in houses:
            teams = h.teams
            t_ids = [t.id for t in teams]
            
            r1_pend = Team.query.filter_by(house_id=h.id, round1_status='requested').count()
            r1_comp = Team.query.filter(Team.house_id == h.id, Team.round1_status.in_(['verified', 'approved'])).count()
            r2_start = Team.query.filter_by(house_id=h.id, current_round=2, round2_completed=False).count()
            r2_fin = Team.query.filter_by(house_id=h.id, round2_completed=True).count()
            
            scans = 0
            if t_ids:
                scans = QRScanLog.query.filter(QRScanLog.team_id.in_(t_ids)).count()
                
            data.append([
                h.name,
                len(teams),
                r1_pend,
                r1_comp,
                r2_start,
                r2_fin,
                scans
            ])
        return headers, data
        
    elif report_type == 'leaderboard':
        headers = ['Rank', 'Team Name', 'House', 'Current Round', 'Completion Time', 'QR Clues Progress']
        data = []
        
        # Sorting priority:
        # 1. Completed Round 2 (round2_completed == True) sorted by round2_completion_time ascending.
        # 2. In Round 2 sorted by round2_current_clue descending, then by Round 1 approved_at timestamp ascending.
        # 3. In Round 1 sorted by completed tasks count descending, then by team creation time ascending.
        
        teams = Team.query.all()
        
        def sort_key(t):
            # We want to sort ascending, so lower sort score represents higher rank
            if t.round2_completed:
                # Rank 1: completed all clues. Sort by round 2 completion time
                t_val = t.round2_completion_time.timestamp() if t.round2_completion_time else 0
                return (0, t_val)
            elif t.current_round == 2:
                # Rank 2: in Round 2. Sort by current expected clue descending (negative), then by R1 approval time
                r1_app = Round1Approval.query.filter_by(team_id=t.id).first()
                r1_time = r1_app.approved_at.timestamp() if (r1_app and r1_app.approved_at) else t.created_at.timestamp()
                return (1, -t.round2_current_clue, r1_time)
            else:
                # Rank 3: in Round 1. Sort by completed task counts descending (negative), then creation time
                comp_count = len(t.task_completions)
                return (2, -comp_count, t.created_at.timestamp())
                
        sorted_teams = sorted(teams, key=sort_key)
        
        for rank, t in enumerate(sorted_teams, start=1):
            comp_time = "-"
            if t.round2_completed and t.round2_completion_time:
                comp_time = t.round2_completion_time.strftime('%Y-%m-%d %H:%M:%S')
            elif t.current_round == 2:
                r1_app = Round1Approval.query.filter_by(team_id=t.id).first()
                if r1_app and r1_app.approved_at:
                    comp_time = f"R1 Appr: {r1_app.approved_at.strftime('%Y-%m-%d %H:%M:%S')}"
                    
            qr_prog = f"{t.round2_current_clue - 1} / 7 solved" if t.current_round == 2 else "Not started"
            if t.round2_completed:
                qr_prog = "7 / 7 solved"
                
            data.append([
                rank,
                t.team_name,
                t.house.name,
                f"Round {t.current_round}",
                comp_time,
                qr_prog
            ])
        return headers, data
        
    elif report_type == 'qr_analytics':
        headers = ['Timestamp', 'Team Name', 'House', 'Clue Target', 'Scan Type', 'IP Address', 'Browser']
        data = []
        logs = QRScanLog.query.order_by(QRScanLog.timestamp.desc()).all()
        for log in logs:
            scan_type = "Correct"
            if log.is_dummy:
                scan_type = "Dummy Clue"
            elif log.is_repeated:
                scan_type = "Repeated Scan"
            elif not log.qr_code_id:
                scan_type = "Invalid Token"
            elif not log.is_correct:
                scan_type = "Out of Order"
                
            clue_tgt = f"Clue {log.qr_code.clue_number}" if log.qr_code else "None"
            if log.is_dummy:
                clue_tgt = "Dummy Clue"
                
            data.append([
                log.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
                log.team.team_name,
                log.team.house.name,
                clue_tgt,
                scan_type,
                log.ip_address or "-",
                log.browser[:30] + "..." if log.browser else "-"
            ])
        return headers, data
        
    elif report_type == 'team_progress':
        headers = ['Team Name', 'House', 'Leader Phone', 'R1 Tasks Done', 'R1 Status', 'R2 Progress', 'Last Activity']
        data = []
        teams = Team.query.all()
        for t in teams:
            r1_done = f"{len(t.task_completions)} / 10"
            r2_prog = f"{t.round2_current_clue - 1} / 7" if t.current_round == 2 else "Locked"
            if t.round2_completed:
                r2_prog = "Completed"
                
            # Find last activity time
            last_act = "-"
            last_scan = QRScanLog.query.filter_by(team_id=t.id).order_by(QRScanLog.timestamp.desc()).first()
            last_task = TaskCompletion.query.filter_by(team_id=t.id).order_by(TaskCompletion.completed_at.desc()).first()
            
            times = []
            if last_scan: times.append(last_scan.timestamp)
            if last_task: times.append(last_task.completed_at)
            
            if times:
                last_act = max(times).strftime('%Y-%m-%d %H:%M:%S')
            else:
                last_act = t.created_at.strftime('%Y-%m-%d %H:%M:%S')
                
            data.append([
                t.team_name,
                t.house.name,
                t.leader_phone,
                r1_done,
                t.round1_status.upper(),
                r2_prog,
                last_act
            ])
        return headers, data
        
    return None, None
