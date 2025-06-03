from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user

from app.constants import RADIONUCLIDE_TABLE
from app.table_manager import get_table_manager

radionuclide_bp = Blueprint('radionuclides', __name__, template_folder='templates')

DEFAULT_NUCLIDES = [
    ('18F', 109.8),
    ('68Ga', 67.7),
    ('11C', 20.4),
    ('15O', 2.03),
    ('13N', 9.96),
]


@login_required
@radionuclide_bp.route('/radionuclides', methods=['GET'])
def manage_radionuclides():
    table_mgr = get_table_manager()
    username = current_user.username

    radionuclides = list(table_mgr.query_entities(RADIONUCLIDE_TABLE, f"PartitionKey eq '{username}'"))
    if not radionuclides:
        batch = [
            {
                'PartitionKey': username,
                'RowKey': name,
                'half_life': half_life,
            }
            for name, half_life in DEFAULT_NUCLIDES
        ]
        table_mgr.upload_batch_to_table(RADIONUCLIDE_TABLE, batch)
        radionuclides = batch

    radionuclides = sorted(radionuclides, key=lambda r: r['RowKey'])
    return render_template('radionuclide.html', radionuclides=radionuclides)


@login_required
@radionuclide_bp.route('/radionuclides/add', methods=['GET', 'POST'])
def add_radionuclide():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        try:
            half_life = float(request.form.get('half_life'))
        except (ValueError, TypeError):
            flash('Half life must be a number.', 'error')
            return redirect(url_for('radionuclides.add_radionuclide'))

        if not name:
            flash('Name is required.', 'error')
            return redirect(url_for('radionuclides.add_radionuclide'))

        table_mgr = get_table_manager()
        username = current_user.username
        entity = {
            'PartitionKey': username,
            'RowKey': name,
            'half_life': half_life,
        }
        table_mgr.upload_batch_to_table(RADIONUCLIDE_TABLE, [entity])
        flash('Radionuclide added.', 'success')
        return redirect(url_for('radionuclides.manage_radionuclides'))

    return render_template('add_radionuclide.html')


@login_required
@radionuclide_bp.route('/radionuclides/edit/<row_key>', methods=['GET', 'POST'])
def edit_radionuclide(row_key):
    table_mgr = get_table_manager()
    username = current_user.username
    entity = table_mgr.get_entity(RADIONUCLIDE_TABLE, username, row_key)
    if not entity:
        flash('Radionuclide not found.', 'error')
        return redirect(url_for('radionuclides.manage_radionuclides'))

    if request.method == 'POST':
        try:
            entity['half_life'] = float(request.form.get('half_life'))
        except (ValueError, TypeError):
            flash('Half life must be a number.', 'error')
            return redirect(url_for('radionuclides.edit_radionuclide', row_key=row_key))
        table_mgr.upload_batch_to_table(RADIONUCLIDE_TABLE, [entity])
        flash('Radionuclide updated.', 'success')
        return redirect(url_for('radionuclides.manage_radionuclides'))

    return render_template('edit_radionuclide.html', radionuclide=entity)


@login_required
@radionuclide_bp.route('/radionuclides/delete/<row_key>', methods=['POST'])
def delete_radionuclide(row_key):
    table_mgr = get_table_manager()
    username = current_user.username
    entity = table_mgr.get_entity(RADIONUCLIDE_TABLE, username, row_key)
    if entity:
        table_mgr.delete_entities(RADIONUCLIDE_TABLE, [entity])
        flash('Radionuclide deleted.', 'success')
    else:
        flash('Radionuclide not found.', 'error')
    return redirect(url_for('radionuclides.manage_radionuclides'))
