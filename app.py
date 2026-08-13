import eventlet
eventlet.monkey_patch()

import os
import datetime
from flask import Flask, redirect, url_for, render_template, request, session, send_from_directory, flash, jsonify
from flask_login import LoginManager, current_user
from flask_socketio import SocketIO
from config import Config
from models import db, User, Team, House, Task, QRCode, QRScanLog, TaskCompletion, Round1Approval, Round2Progress

socketio = SocketIO()

from flask_socketio import join_room

@socketio.on('join')
def on_join(data):
    room = data.get('room')
    if room:
        join_room(room)

def create_app(config_override=None):
    app = Flask(__name__)
    app.config.from_object(Config)
    if config_override:
        app.config.update(config_override)

    # Initialize extensions
    db.init_app(app)

    # Thread-safe database commit queue to prevent concurrent SQLite write lock contentions
    import threading
    db_write_lock = threading.Lock()
    original_commit = db.session.commit

    def safe_commit():
        with db_write_lock:
            return original_commit()

    db.session.commit = safe_commit

    # Configure SQLite pragmas for high concurrency & robustness
    if 'sqlite' in app.config.get('SQLALCHEMY_DATABASE_URI', ''):
        from sqlalchemy import event
        with app.app_context():
            @event.listens_for(db.engine, 'connect')
            def set_sqlite_pragma(dbapi_connection, connection_record):
                cursor = dbapi_connection.cursor()
                try:
                    cursor.execute("PRAGMA journal_mode=WAL")
                    cursor.execute("PRAGMA synchronous=NORMAL")
                except Exception:
                    pass
                finally:
                    cursor.close()

    # Self-healing database check & seed (skipped in testing mode)
    if not app.config.get('TESTING'):
        with app.app_context():
            try:
                try:
                    with db.engine.begin() as conn:
                        conn.execute(db.text("ALTER TABLE users ALTER COLUMN password_hash TYPE VARCHAR(255)"))
                except Exception:
                    pass
                try:
                    with db.engine.begin() as conn:
                        conn.execute(db.text("ALTER TABLE users ADD COLUMN current_login_token VARCHAR(100)"))
                except Exception:
                    pass
                try:
                    with db.engine.begin() as conn:
                        conn.execute(db.text("ALTER TABLE qr_codes ADD COLUMN image_base64 TEXT"))
                except Exception:
                    pass
                try:
                    with db.engine.begin() as conn:
                        conn.execute(db.text("ALTER TABLE teams ADD COLUMN winner_rank INTEGER"))
                except Exception:
                    pass
                
                from seed import run_seed
                run_seed(drop_tables=False)
            except Exception as e:
                app.logger.warning(f"Self-healing database setup failed: {str(e)}")
    
    # Configure Flask Login
    login_manager = LoginManager()
    login_manager.login_view = 'auth.login'
    login_manager.login_message_category = 'danger'
    login_manager.init_app(app)

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    # Initialize SocketIO with Flask app
    # async_mode is set automatically based on installed packages (eventlet, gevent, threading)
    socketio.init_app(app, cors_allowed_origins="*", async_mode='eventlet')
    app.extensions['socketio'] = socketio

    # Ensure upload and QR directories exist
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    os.makedirs(app.config['QR_FOLDER'], exist_ok=True)

    # Register Blueprints
    from routes.auth import auth_bp
    from routes.team import team_bp
    from routes.manager import manager_bp
    from routes.admin import admin_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(team_bp)
    app.register_blueprint(manager_bp)
    app.register_blueprint(admin_bp)

    @app.before_request
    def check_single_device_session():
        if request.path.startswith('/static/'):
            return
        if request.endpoint in ['auth.login', 'auth.logout', 'auth.register', 'stats', 'stats_logout']:
            return

        if current_user.is_authenticated and current_user.role == 'team_leader':
            sess_token = session.get('login_token')
            db_token = current_user.current_login_token
            
            if sess_token != db_token:
                app.logger.warning(
                    f"Single-device login violation for user '{current_user.username}': "
                    f"session_token={sess_token}, db_token={db_token}. Forcing logout."
                )
                from flask_login import logout_user
                logout_user()
                session.clear()
                flash("Your account was logged in from another device or session expired.", "warning")
                return redirect(url_for('auth.login'))

    # Base routing
    @app.route('/')
    def index():
        if current_user.is_authenticated:
            if current_user.role == 'admin':
                return redirect(url_for('admin.dashboard'))
            elif current_user.role == 'manager':
                return redirect(url_for('manager.dashboard'))
            elif current_user.role == 'team_leader':
                return redirect(url_for('team.dashboard'))
        return redirect(url_for('auth.login'))

    # Serve uploads safely (especially useful for local run or simple cloud containers)
    @app.route('/static/uploads/<path:filename>')
    def serve_uploads(filename):
        return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

    @app.route('/static/qrs/<path:filename>')
    def serve_qrs(filename):
        return send_from_directory(app.config['QR_FOLDER'], filename)

    # Statistics / Leaderboard page with password gate
    @app.route('/stats', methods=['GET', 'POST'])
    def stats():
        # Check if password is already authorized in session
        if session.get('stats_authorized') == True:
            return render_stats_page()

        if request.method == 'POST':
            pwd = request.form.get('stats_password', '').strip()
            if pwd == app.config['STATS_PASSWORD']:
                session['stats_authorized'] = True
                return render_stats_page()
            else:
                flash("Incorrect Statistics password.", "danger")

        return render_template('stats_login.html')

    @app.route('/stats/logout')
    def stats_logout():
        session.pop('stats_authorized', None)
        flash("Logged out of statistics dashboard.", "info")
        return redirect(url_for('stats'))

    # API endpoints for stats charts data
    @app.route('/api/stats/data')
    def get_stats_data():
        """Returns computed data for Chart.js dashboards and grids."""
        # 1. House Distribution
        houses = House.query.all()
        house_dist = {h.name: Team.query.filter_by(house_id=h.id).count() for h in houses}
        
        # 2. Round Completion Counts
        r1_active = Team.query.filter_by(current_round=1, round1_status='active').count()
        r1_pending = Team.query.filter_by(round1_status='requested').count()
        r1_verified = Team.query.filter_by(round1_status='verified').count()
        r2_active = Team.query.filter_by(current_round=2, round2_completed=False).count()
        r2_completed = Team.query.filter_by(round2_completed=True).count()
        
        # 3. QR Scan Frequency (clues 1 to 7)
        qr_scans = {}
        for clue_num in range(1, 8):
            qr = QRCode.query.filter_by(clue_number=clue_num, is_dummy=False).first()
            if qr:
                qr_scans[f"Clue {clue_num}"] = QRScanLog.query.filter_by(qr_code_id=qr.id).count()
            else:
                qr_scans[f"Clue {clue_num}"] = 0
        
        # 4. Extra stats card metrics
        all_teams = Team.query.all()
        total_scans = QRScanLog.query.count()
        
        # House Rankings based on team counts and completion averages
        house_ranks = []
        for h in houses:
            teams = h.teams
            t_ids = [t.id for t in teams]
            h_scans = QRScanLog.query.filter(QRScanLog.team_id.in_(t_ids)).count() if t_ids else 0
            h_fin = Team.query.filter_by(house_id=h.id, round2_completed=True).count()
            house_ranks.append({
                'name': h.name,
                'finished': h_fin,
                'scans': h_scans
            })
        house_ranks = sorted(house_ranks, key=lambda x: (-x['finished'], x['scans']))

        # Find fastest team (earliest R2 completion time)
        fastest_team = "None"
        fastest_t = Team.query.filter_by(round2_completed=True).order_by(Team.round2_completion_time.asc()).first()
        if fastest_t:
            fastest_team = f"{fastest_t.team_name} ({fastest_t.house.name})"

        # Find most active team (highest total QR scan attempts)
        most_active_team = "None"
        active_counts = db.session.query(QRScanLog.team_id, db.func.count(QRScanLog.id).label('scan_count'))\
                                  .group_by(QRScanLog.team_id)\
                                  .order_by(db.desc('scan_count')).first()
        if active_counts:
            team_id, s_count = active_counts
            active_t = Team.query.get(team_id)
            if active_t:
                most_active_team = f"{active_t.team_name} ({s_count} scans)"

        # Compute average Round 1 completion time (seconds from creation to approval)
        from routes.admin import get_report_data
        headers, leaderboard_data = get_report_data('leaderboard')
        
        # Build detailed progress list for all teams to support color coding and progress bars
        teams_list_data = []
        for t in all_teams:
            r1_comp_count = len(t.task_completions)
            r1_pct = int((r1_comp_count / 10) * 100)
            
            r2_pct = int(((t.round2_current_clue - 1) / 7) * 100) if t.current_round == 2 else 0
            if t.round2_completed:
                r2_pct = 100
                
            last_act = "Never"
            last_scan = QRScanLog.query.filter_by(team_id=t.id).order_by(QRScanLog.timestamp.desc()).first()
            last_task = TaskCompletion.query.filter_by(team_id=t.id).order_by(TaskCompletion.completed_at.desc()).first()
            
            times = []
            if last_scan: times.append(last_scan.timestamp)
            if last_task: times.append(last_task.completed_at)
            
            if times:
                last_act = max(times).strftime('%Y-%m-%d %H:%M:%S')
            else:
                last_act = t.created_at.strftime('%Y-%m-%d %H:%M:%S')
                
            r1_app = Round1Approval.query.filter_by(team_id=t.id).first()
            is_logged = t.user.current_login_token is not None
            teams_list_data.append({
                'id': t.id,
                'team_name': t.team_name,
                'house_name': t.house.name,
                'current_round': t.current_round,
                'round1_status': t.round1_status,
                'round1_pct': r1_pct,
                'round2_pct': r2_pct,
                'round2_clue': t.round2_current_clue,
                'round2_completed': t.round2_completed,
                'winner_rank': t.winner_rank,
                'is_logged_in': is_logged,
                'last_active': last_act,
                'created_iso': t.created_at.isoformat() + "Z" if t.created_at else "",
                'approved_iso': r1_app.approved_at.isoformat() + "Z" if (r1_app and r1_app.approved_at) else "",
                'finished_iso': t.round2_completion_time.isoformat() + "Z" if t.round2_completion_time else ""
            })
            
        # Check if any team has started Round 1 yet
        game_started = Team.query.filter(Team.round1_status != 'pending_start').count() > 0
        
        # Find earliest start time among teams
        earliest_start = None
        for t in all_teams:
            if t.round1_status != 'pending_start':
                r1_app = Round1Approval.query.filter_by(team_id=t.id).first()
                start_time = r1_app.approved_at if (r1_app and r1_app.approved_at) else t.created_at
                if start_time:
                    if earliest_start is None or start_time < earliest_start:
                        earliest_start = start_time
        earliest_start_iso = earliest_start.isoformat() + "Z" if earliest_start else ""
        
        # House status list
        house_status = {}
        for h in houses:
            teams_in_house = [t for t in all_teams if t.house_id == h.id]
            house_teams_data = []
            house_logged_in = True
            
            if not teams_in_house:
                house_logged_in = False
                
            for t in teams_in_house:
                is_logged = t.user.current_login_token is not None
                if not is_logged:
                    house_logged_in = False
                house_teams_data.append({
                    'team_name': t.team_name,
                    'is_logged_in': is_logged
                })
                
            house_status[h.name] = {
                'logged_in': house_logged_in,
                'teams': house_teams_data
            }
            
        return jsonify({
            'game_started': game_started,
            'house_status': house_status,
            'server_now_iso': datetime.datetime.utcnow().isoformat() + "Z",
            'earliest_start_iso': earliest_start_iso,
            'house_dist': house_dist,
            'round_stats': {
                'R1 Active': r1_active,
                'R1 Pending': r1_pending,
                'R1 Verified': r1_verified,
                'R2 Active': r2_active,
                'R2 Completed': r2_completed
            },
            'qr_scans': qr_scans,
            'metrics': {
                'total_scans': total_scans,
                'fastest_team': fastest_team,
                'most_active_team': most_active_team,
                'house_rankings': house_ranks
            },
            'leaderboard': leaderboard_data,
            'teams_progress': teams_list_data
        })

    def render_stats_page():
        # Check if at least one team has completed Round 1 (round1_status is verified or approved, or round 2 completed, etc.)
        # The prompt says: "Initially LEFT SIDE occupies 100%. Right side hidden... After first completion [of Round 1] split screen 40% Left 60% Right"
        # First completion of Round 1 means round1_status in ['verified', 'approved'] or current_round == 2 for at least one team.
        # Let's count teams who finished Round 1 (status is approved)
        r1_finished_count = Team.query.filter(Team.round1_status.in_(['approved'])).count()
        split_enabled = r1_finished_count > 0

        # Load all teams for Left side table
        teams = Team.query.all()
        
        # Check if any team has started Round 1 yet
        game_started = Team.query.filter(Team.round1_status != 'pending_start').count() > 0
        
        # House status list
        houses = House.query.all()
        house_status = {}
        for h in houses:
            teams_in_house = [t for t in teams if t.house_id == h.id]
            house_teams_data = []
            house_logged_in = True
            
            if not teams_in_house:
                house_logged_in = False
                
            for t in teams_in_house:
                is_logged = t.user.current_login_token is not None
                if not is_logged:
                    house_logged_in = False
                house_teams_data.append({
                    'team_name': t.team_name,
                    'is_logged_in': is_logged
                })
                
            house_status[h.name] = {
                'logged_in': house_logged_in,
                'teams': house_teams_data
            }
        
        return render_template('stats.html', 
                               teams=teams, 
                               split_enabled=split_enabled,
                               game_started=game_started,
                               house_status=house_status)

    return app

if __name__ == '__main__':
    app = create_app()
    # Disable debug mode by default to prevent reloader from crashing the port binding under concurrent execution
    debug_mode = os.environ.get('FLASK_DEBUG', 'false').lower() in ['true', '1']
    socketio.run(app, debug=debug_mode, host='0.0.0.0', port=5000)
