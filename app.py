# ---------- DEPENDENCIES ---------- #
# External dependencies
import os
import openai
import jwt
import io
from time import time
from datetime import datetime
from dotenv import load_dotenv, find_dotenv
from flask import Flask, render_template, request, url_for, redirect, flash, send_file, Response, session
from flask_sqlalchemy import SQLAlchemy, pagination
from flask_login import UserMixin, login_user, LoginManager, login_required, logout_user, current_user
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField
from wtforms.validators import InputRequired, Length, ValidationError, Email, EqualTo
from flask_bcrypt import Bcrypt
from flask_mail import Mail, Message


# Internal dependencies
from prompt_template import prompt_template

# ---------- FLASK APP AND DATABASE ---------- #
# Instantiating Flask class
app = Flask(__name__)

# Defining the path to the database
root_folder = os.path.dirname(os.path.abspath(__file__))
database_path = os.path.join(root_folder, 'instance', 'database.db')

# Configuring the database
app.config['SQLALCHEMY_DATABASE_URI'] = f"sqlite:///{database_path}"  # Connecting app to the database
# REFACTOR: SECRET KEY SHOULDN'T BE VISIBLE IN A PRODUCTION ENVIRONMENT
app.config['SECRET_KEY'] = 'thisisasecretkey'
db = SQLAlchemy(app)  # Creating database

# Instantiating Bcrypt
bcrypt = Bcrypt(app)

# ---------- FLASK MAIL ---------- #
app.config['MAIL_SERVER'] = 'smtp.googlemail.com'  # or your preferred SMTP server
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = os.environ.get('EMAIL_USER')
app.config['MAIL_PASSWORD'] = os.environ.get('EMAIL_PASS')

mail = Mail(app)

# ---------- LOG IN MANAGER ---------- #
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# ---------- SEND RESET EMAIL FUNCTION ---------- #
def send_reset_email(user):
    token = user.get_reset_token()
    msg = Message('Password Reset Request',
                  sender='noreply@demo.com',
                  recipients=[user.email])
    msg.body = f'''To reset your password, visit the following link:
{url_for('reset_token', token=token, _external=True)}
If you did not make this request then simply ignore this email and no changes will be made.
'''
    mail.send(msg)

# ---------- CREATING CLASSES AND FORMS ---------- #
class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(50), nullable=False, unique=True)
    password = db.Column(db.String(100), nullable=False)
    api_key = db.Column(db.String(200))
    cv = db.Column(db.String(5000), default='')

    def get_reset_token(self, expires_sec=1800):
        payload = {
            'user_id': self.id,
            'exp': time() + expires_sec
        }
        return jwt.encode(payload, app.config['SECRET_KEY'], algorithm='HS256')

    @staticmethod
    def verify_reset_token(token):
        try:
            payload = jwt.decode(token, app.config['SECRET_KEY'], algorithms=['HS256'])
            user_id = payload['user_id']
        except jwt.ExpiredSignatureError:
            return None
        except jwt.InvalidTokenError:
            return None
        return User.query.get(user_id)

class SignupForm(FlaskForm):
    email = StringField(validators=[
                           InputRequired(), Email(), Length(min=4, max=50)], render_kw={"placeholder": "Email *"})

    password = PasswordField(validators=[
                             InputRequired(), Length(min=4, max=50)], render_kw={"placeholder": "Password *"})

    submit = SubmitField('Signup')

    def validate_email(self, email):
        existing_user_email = User.query.filter_by(
            email=email.data).first()
        if existing_user_email:
            raise ValidationError(
                'That user e-mail already exists. Please choose a different one.')


class LoginForm(FlaskForm):
    email = StringField(validators=[
                           InputRequired(), Length(min=4, max=50)], render_kw={"placeholder": "Email *"})

    password = PasswordField(validators=[
                             InputRequired(), Length(min=4, max=50)], render_kw={"placeholder": "Password *"})

    submit = SubmitField('Login')


class RequestResetForm(FlaskForm):
    email = StringField('Email', validators=[InputRequired(), Email()])
    submit = SubmitField('Request Password Reset')


class ResetPasswordForm(FlaskForm):
    password = PasswordField('Password', validators=[InputRequired()])
    confirm_password = PasswordField('Confirm Password', validators=[InputRequired(), EqualTo('password')])
    submit = SubmitField('Reset Password')


class Log(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    job_title = db.Column(db.String(100), nullable=False)
    employer_name = db.Column(db.String(100), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

    def __repr__(self):
        return f"Log('{self.timestamp}', '{self.employer_name}', '{self.job_title}')"


#---------- ROUTES ----------
@app.route('/', methods=['GET', 'POST'])
def home():
    form = SignupForm()

    if form.validate_on_submit():
        hashed_password = bcrypt.generate_password_hash(form.password.data).decode('utf-8')
        user = User(email=form.email.data, password=hashed_password)
        db.session.add(user)
        db.session.commit()
        flash('Your account has been created! You are now able to log in', 'success')
        return redirect(url_for('login'))
    return render_template('signup.html', title='Register', form=form)


@app.route('/login', methods=['GET', 'POST'])
def login():
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data).first()
        if user:
            if bcrypt.check_password_hash(user.password, form.password.data):
                login_user(user)
                return redirect(url_for('dashboard'))
    return render_template('login.html', form=form)

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))


@app.route('/dashboard', methods=['GET', 'POST'])
@login_required
def dashboard():
    if request.method == 'POST':
        # Handle API key form submission
        new_api_key = request.form.get('api_key')
        if new_api_key:
            hashed_api_key = bcrypt.generate_password_hash(new_api_key)
            current_user.api_key = hashed_api_key

        # Handle CV form submission
        new_cv = request.form.get('cv')
        if new_cv:
            current_user.cv = new_cv

        try:
            db.session.commit()
        except Exception as e:
            # Log the error and show an error message to the user
            print(e)
            flash('An error occurred while updating your data. Please try again.', 'error')

    # If the user's CV is empty, show a placeholder
    cv = current_user.cv if current_user.cv else "Your CV goes here"

    # Fetch the logs from the database
    page = request.args.get('page', 1, type=int)
    per_page = 10
    logs = Log.query.filter_by(user_id=current_user.id).order_by(Log.timestamp.desc()).paginate(page=page, per_page=per_page)
    # Fetch all logs for download
    all_logs = Log.query.filter_by(user_id=current_user.id).order_by(Log.timestamp.desc()).all()

    # Downloading logs
    if 'download_logs' in request.form:
        # Create a string representation of the logs
        logs_str = "\n".join(f"{log.timestamp}\t{log.job_title}\t{log.employer_name}" for log in all_logs)
        str_io = io.StringIO()
        str_io.write(logs_str)
        str_io.seek(0)
        filename = f"ai_cover_letter_log_{datetime.now().strftime('%Y%m%d%H%M%S')}.txt"
        return Response(
            str_io.getvalue(),
            mimetype="text/plain",
            headers={"Content-disposition":
                     f"attachment; filename={filename}"})

    return render_template('dashboard.html', user=current_user, cv=cv, logs=logs)


@app.route('/generator', methods=['GET', 'POST'])
@login_required
def generator():
    # Check if the user's API key is set
    if not current_user.api_key:
        flash('Please set your API key before generating a cover letter.', 'error')
        return redirect(url_for('dashboard'))

    response = ""
    job_title = ""
    job_description = ""
    employer_name = ""
    employer_description = ""
    additional_instructions = ""

    if request.method == 'POST':
        cv = current_user.cv
        job_title = request.form.get('job_title')
        job_description = request.form.get('job_description')
        employer_name = request.form.get('employer_name')
        employer_description = request.form.get('employer_description')
        additional_instructions = request.form.get('additional_instructions')

    if 'generate' in request.form:
        prompt = prompt_template.format(cv=cv, job_title=job_title, job_description=job_description,
                                        employer_name=employer_name, employer_description=employer_description,
                                        additional_instructions=additional_instructions)
        response = get_completion(prompt)

        # Save the response in the user's session
        session['response'] = response

        # Create a log entry
        log = Log(job_title=job_title, employer_name=employer_name, user_id=current_user.id)
        db.session.add(log)
        db.session.commit()

    elif 'clear' in request.form:
        job_title = ""
        job_description = ""
        employer_name = ""
        employer_description = ""
        additional_instructions = ""

    if 'download' in request.form:
        # Retrieve the response from the session
        response = session.get('response', '')
        if response == '':
            flash('Please generate a cover letter before downloading.', 'error')
            return redirect(url_for('generator'))

        str_io = io.StringIO()
        str_io.write(response)
        str_io.seek(0)
        # Check if employer_name and job_title are not None, else replace with 'Unknown'
        employer_name = employer_name if employer_name else 'Unknown Employer'
        # Format the filename
        filename = f"{employer_name} - {job_title} - {datetime.now().strftime('%Y.%m.%d')}.txt"
        return Response(
            str_io.getvalue(),
            mimetype="text/plain",
            headers={"Content-disposition":
                         f"attachment; filename={filename}"})

    return render_template('generator.html', response=response, job_title=job_title, job_description=job_description,
                           employer_name=employer_name, employer_description=employer_description,
                           additional_instructions=additional_instructions)


@app.route('/reset_request', methods=['GET', 'POST'])
def reset_request():
    form = RequestResetForm()
    message = None
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data).first()
        if user:
            send_reset_email(user)
            message = 'An e-mail has been sent with instructions to reset your password.'
    return render_template('reset_request.html', form=form, message=message)


@app.route('/reset_request/<token>', methods=['GET', 'POST'])
def reset_token(token):
    user = User.verify_reset_token(token)
    if not user:
        # If the token is invalid or expired, redirect the user to the `reset_request` route.
        return redirect(url_for('reset_request'))
    form = ResetPasswordForm()
    if form.validate_on_submit():
        hashed_password = bcrypt.generate_password_hash(form.password.data)
        user.password = hashed_password
        db.session.commit()
        return redirect(url_for('login'))
    return render_template('reset_token.html', form=form)


# ---------- ENVIRONMENT VARIABLE API KEY ---------- #
# Setting up environment
_ = load_dotenv(find_dotenv())  # read local .env file
openai.api_key = os.getenv('OPENAI_API_KEY')


# ---------- GPT HELPER FUNCTION ---------- #
# Defining the helper function
def get_completion(prompt, model="gpt-3.5-turbo"):
    messages = [{"role": "user", "content": prompt}]
    response = openai.ChatCompletion.create(
        model=model,
        messages=messages,
        temperature=0,  # this is the degree of randomness of the model's output
    )
    return response.choices[0].message["content"]

# Checking if database exists and creating a new one if it doesnt
with app.app_context():
    try:
        db.create_all()
        print("Tables created.")
    except Exception as e:
        print("An error occurred while creating tables:", e)

# # TEMPORARY
# @app.after_request
# def add_header(response):
#     response.cache_control.no_store = True
#     return response


# ---------- END ---------- #
if __name__ == '__main__':
    app.run(debug=True)
