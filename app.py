from flask import Flask, render_template, request, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
from functools import wraps
import os
from models import db, User, Subject, Task

app = Flask(__name__)

# Read DATABASE_URL from environment (Vercel + Neon), fall back to SQLite locally
database_url = os.environ.get('DATABASE_URL', 'sqlite:///study.db')

# Neon/Heroku sometimes give postgres:// which SQLAlchemy rejects
if database_url.startswith('postgres://'):
    database_url = database_url.replace('postgres://', 'postgresql://', 1)

app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'change-this-to-a-random-secret-key')
app.config['SQLALCHEMY_DATABASE_URI'] = database_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)

with app.app_context():
    db.create_all()


# ---------- Helpers ----------
def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please log in first.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return wrapper


def current_user():
    return User.query.get(session['user_id'])


# ---------- Auth routes ----------
@app.route('/')
def index():
    return redirect(url_for('dashboard') if 'user_id' in session else url_for('login'))


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username'].strip()
        email = request.form['email'].strip().lower()
        password = request.form['password']

        if not username or not email or not password:
            flash('All fields are required.', 'danger')
            return redirect(url_for('register'))

        if User.query.filter((User.username == username) | (User.email == email)).first():
            flash('Username or email already exists.', 'danger')
            return redirect(url_for('register'))

        user = User(
            username=username,
            email=email,
            password_hash=generate_password_hash(password)
        )
        db.session.add(user)
        db.session.commit()
        flash('Account created! Please log in.', 'success')
        return redirect(url_for('login'))

    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password']
        user = User.query.filter_by(username=username).first()

        if user and check_password_hash(user.password_hash, password):
            session['user_id'] = user.id
            session['username'] = user.username
            flash(f'Welcome back, {user.username}!', 'success')
            return redirect(url_for('dashboard'))

        flash('Invalid credentials.', 'danger')

    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    flash('Logged out.', 'info')
    return redirect(url_for('login'))


# ---------- Dashboard ----------
@app.route('/dashboard')
@login_required
def dashboard():
    user = current_user()
    now = datetime.utcnow()

    all_tasks = Task.query.filter_by(user_id=user.id).all()
    upcoming = (Task.query
                .filter_by(user_id=user.id, completed=False)
                .filter(Task.deadline >= now)
                .order_by(Task.deadline.asc())
                .limit(5).all())
    overdue = (Task.query
               .filter_by(user_id=user.id, completed=False)
               .filter(Task.deadline < now)
               .order_by(Task.deadline.asc()).all())

    stats = {
        'total': len(all_tasks),
        'completed': sum(1 for t in all_tasks if t.completed),
        'pending': sum(1 for t in all_tasks if not t.completed),
        'overdue': len(overdue),
    }

    return render_template('dashboard.html',
                           user=user, upcoming=upcoming,
                           overdue=overdue, stats=stats, now=now)


# ---------- Subjects ----------
@app.route('/subjects', methods=['GET', 'POST'])
@login_required
def subjects():
    user = current_user()
    if request.method == 'POST':
        name = request.form['name'].strip()
        color = request.form.get('color', '#0d6efd')
        if name:
            db.session.add(Subject(name=name, color=color, user_id=user.id))
            db.session.commit()
            flash('Subject added.', 'success')
        return redirect(url_for('subjects'))

    user_subjects = Subject.query.filter_by(user_id=user.id).all()
    return render_template('subjects.html', subjects=user_subjects)


@app.route('/subjects/<int:subject_id>/delete', methods=['POST'])
@login_required
def delete_subject(subject_id):
    subj = Subject.query.filter_by(id=subject_id, user_id=session['user_id']).first_or_404()
    db.session.delete(subj)
    db.session.commit()
    flash('Subject and its tasks deleted.', 'info')
    return redirect(url_for('subjects'))


# ---------- Tasks ----------
@app.route('/tasks', methods=['GET', 'POST'])
@login_required
def tasks():
    user = current_user()
    user_subjects = Subject.query.filter_by(user_id=user.id).all()

    if request.method == 'POST':
        title = request.form['title'].strip()
        description = request.form.get('description', '').strip()
        deadline_str = request.form['deadline']
        subject_id = request.form.get('subject_id')

        if not title or not deadline_str or not subject_id:
            flash('Title, deadline, and subject are required.', 'danger')
            return redirect(url_for('tasks'))

        subject = Subject.query.filter_by(id=subject_id, user_id=user.id).first()
        if not subject:
            flash('Invalid subject.', 'danger')
            return redirect(url_for('tasks'))

        try:
            deadline = datetime.strptime(deadline_str, '%Y-%m-%dT%H:%M')
        except ValueError:
            flash('Invalid deadline format.', 'danger')
            return redirect(url_for('tasks'))

        db.session.add(Task(
            title=title, description=description,
            deadline=deadline, subject_id=subject.id, user_id=user.id
        ))
        db.session.commit()
        flash('Task added.', 'success')
        return redirect(url_for('tasks'))

    # Filtering
    filter_by = request.args.get('filter', 'all')
    query = Task.query.filter_by(user_id=user.id)
    now = datetime.utcnow()

    if filter_by == 'pending':
        query = query.filter_by(completed=False)
    elif filter_by == 'completed':
        query = query.filter_by(completed=True)
    elif filter_by == 'overdue':
        query = query.filter_by(completed=False).filter(Task.deadline < now)

    all_tasks = query.order_by(Task.completed.asc(), Task.deadline.asc()).all()

    return render_template('tasks.html',
                           tasks=all_tasks, subjects=user_subjects,
                           now=now, filter_by=filter_by)


@app.route('/tasks/<int:task_id>/toggle', methods=['POST'])
@login_required
def toggle_task(task_id):
    task = Task.query.filter_by(id=task_id, user_id=session['user_id']).first_or_404()
    task.completed = not task.completed
    db.session.commit()
    return redirect(request.referrer or url_for('tasks'))


@app.route('/tasks/<int:task_id>/delete', methods=['POST'])
@login_required
def delete_task(task_id):
    task = Task.query.filter_by(id=task_id, user_id=session['user_id']).first_or_404()
    db.session.delete(task)
    db.session.commit()
    flash('Task deleted.', 'info')
    return redirect(request.referrer or url_for('tasks'))


if __name__ == '__main__':
    app.run(debug=True)
