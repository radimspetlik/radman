from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
import json
from app.constants import PHARM_TABLE, DAYSETUP_TABLE
from app.table_manager import get_table_manager
import matplotlib.pyplot as plt
import io
import base64
from datetime import datetime, timedelta
import math

daysetup_bp = Blueprint('daysetup', __name__, template_folder='templates')

# Ge-68 half-life in days (approximately 270.8 days)
GE68_HALF_LIFE = 270.8

def create_decay_plot(activity0, gen_date):
    # Determine decay constant: lambda = ln(2)/half-life
    decay_constant = math.log(2) / GE68_HALF_LIFE

    # Create a time series: from generator date to a bit beyond current date
    start_date = gen_date
    end_date = gen_date + timedelta(days=int(2 * GE68_HALF_LIFE))
    num_days = (end_date - start_date).days + 1

    # Generate daily time points and corresponding activities
    dates = [start_date + timedelta(days=i) for i in range(num_days)]
    t_days = [(d - gen_date).days for d in dates]
    activities = [activity0 * math.exp(-decay_constant * t) for t in t_days]

    # Determine current date (state) and compute its activity if within range
    current_date = datetime.now().date()
    current_dt = datetime.combine(current_date, datetime.min.time())
    if start_date <= current_dt <= end_date:
        t_current = (current_dt - gen_date).days
        current_activity = activity0 * math.exp(-decay_constant * t_current)
    else:
        t_current = None

    # Begin plot creation
    plt.figure(figsize=(8, 4))
    ax = plt.gca()
    line, = plt.plot(dates, activities, label='Decay curve')

    # Mark and annotate the current date if available
    if t_current is not None:
        red_dot, = plt.plot(current_dt, current_activity, 'ro', label='Today')
        # Annotate with the current activity value (formatted to 2 decimal places)
        plt.annotate(f"{current_activity:.2f} GBq",
                     xy=(current_dt, current_activity),
                     xytext=(10, 10), textcoords='offset points',
                     color='white', fontsize=10,
                     arrowprops=dict(arrowstyle="->", color='white'))

    plt.xlabel('Date', color='white')
    plt.ylabel('Activity (GBq)', color='white')
    plt.title('Ge‑68/Ga‑68 Generator Decay', color='white')
    plt.grid(color='gray', linestyle='--', linewidth=0.5)
    plt.legend()

    # Set ticks and spine colors to white
    ax.tick_params(axis='both', colors='white', labelcolor='white')
    for spine in ax.spines.values():
        spine.set_color('white')

    plt.tight_layout()

    # Save plot to an SVG image in memory with transparent background.
    buf = io.BytesIO()
    plt.savefig(buf, format='svg', transparent=True)
    buf.seek(0)
    plt.close()
    # Encode the SVG image to a base64 string for HTML embedding.
    graph_url = base64.b64encode(buf.getvalue()).decode('utf-8')
    return graph_url

@login_required
@daysetup_bp.route('/daysetup', methods=['GET', 'POST'])
def plan_daysetup():
    user_id = current_user.username
    table_manager = get_table_manager()

    if request.method == 'POST':
        # Retrieve and validate inputs.
        try:
            generator_activity = float(request.form.get('generator_activity', 1.85))
        except (ValueError, TypeError):
            flash("Generator Activity must be a number.", "error")
            return redirect(url_for('daysetup.plan_daysetup'))

        # Get the generator date from the form; default to today's date if parsing fails.
        generator_date_str = request.form.get('generator_date')
        try:
            generator_date = datetime.strptime(generator_date_str, "%Y-%m-%d")
        except (ValueError, TypeError):
            generator_date = datetime.now()
            flash("Invalid date provided; defaulting to today's date.", "warning")

        # Build the record entity to store.
        entity = {
            'PartitionKey': user_id,
            'RowKey': 'default',  # constant RowKey to store one setup per user
            'GeneratorActivity': generator_activity,
            'GeneratorDate': generator_date.strftime("%Y-%m-%d")
        }

        # Save (upsert) the day setup record.
        table_manager.upload_batch_to_table(DAYSETUP_TABLE, [entity])
        flash("Day setup saved successfully.", "success")
        return redirect(url_for('daysetup.plan_daysetup'))

    # For GET requests, load an existing day setup record.
    daysetup_record = table_manager.get_entity(DAYSETUP_TABLE, user_id, "default")
    if daysetup_record:
        try:
            generator_activity = float(daysetup_record.get('GeneratorActivity', 1.85))
        except (ValueError, TypeError):
            generator_activity = 1.85
        generator_date_str = daysetup_record.get('GeneratorDate')
        try:
            generator_date = datetime.strptime(generator_date_str, "%Y-%m-%d")
        except (ValueError, TypeError):
            generator_date = datetime.now()
    else:
        generator_activity = 1.85
        generator_date = datetime.now()

    # Create a decay plot based on stored or default generator date and activity.
    decay_plot_url = create_decay_plot(generator_activity, generator_date)
    generator_date_str = generator_date.strftime("%Y-%m-%d")

    return render_template("daysetup.html",
                           generator_activity=generator_activity,
                           generator_date=generator_date_str,
                           decay_plot_url=decay_plot_url)
