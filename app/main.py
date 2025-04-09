import os
import uuid
from flask import Flask, render_template, request, redirect, url_for, flash
from azure.data.tables import TableEntity

from app.table_manager import get_table_manager
from app.blob_manager import get_blob_manager

app = Flask(__name__)
app.secret_key = "CHANGE_THIS_TO_SOMETHING_SECURE"  # for flashing messages, sessions, etc.

# Constants for Azure Table Storage
MENU_ITEMS_TABLE = "MenuItems"
STATIC_PAGES_TABLE = "StaticPages"
GRAPHS_TABLE = "Graphs"

# We assume these environment variables are set:
#   STORAGE_ACCOUNT_NAME, STORAGE_ACCOUNT_KEY
#   BLOB_CONNECTION_STRING, BLOB_CONTAINER_NAME
# Or you adapt them accordingly for your environment.


# ----------------------------------------------------------------
#  Utility / Initialization
# ----------------------------------------------------------------
def ensure_tables_exist():
    """
    Ensure the required tables exist in Azure Table Storage.
    """
    tm = get_table_manager()
    tm.create_table(MENU_ITEMS_TABLE)
    tm.create_table(STATIC_PAGES_TABLE)
    tm.create_table(GRAPHS_TABLE)

# Create tables at startup (optional)
ensure_tables_exist()


# ----------------------------------------------------------------
#  Helper functions for retrieving menu structure
# ----------------------------------------------------------------
def build_menu_tree():
    """
    Build a nested list/dict structure of menu items from the Azure Table Storage.
    Each menu item has:
      PartitionKey = 'MENU'
      RowKey       = unique ID
      Title        = display name
      Type         = one of ['static', 'graph', 'link']
      LinkRowKey   = if Type == 'link', it references another menu item
      ParentRowKey = parent's RowKey or empty string for top-level
      RefRowKey    = for 'static' or 'graph', which static page or graph it references
    """
    tm = get_table_manager()
    menu_items = list(tm.query_entities(MENU_ITEMS_TABLE, "PartitionKey eq 'MENU'"))

    # Convert to a dict of { row_key : entity_dict }
    by_id = {m['RowKey']: m for m in menu_items}

    # Build adjacency (parent -> [children])
    children_map = {}
    for item in menu_items:
        parent_id = item.get("ParentRowKey", "")
        children_map.setdefault(parent_id, []).append(item)

    # Recursively build the tree from top-level nodes (where parent = "")
    def build_subtree(parent_id):
        nodes = []
        for child in children_map.get(parent_id, []):
            item_id = child['RowKey']
            subtree = build_subtree(item_id)
            # attach children to the node
            child['children'] = subtree
            nodes.append(child)
        return nodes

    tree = build_subtree("")
    return tree, by_id


def get_menu_item(row_key):
    """
    Get one menu item by RowKey from the Azure Table.
    """
    tm = get_table_manager()
    return tm.get_entity(MENU_ITEMS_TABLE, "MENU", row_key)


def get_static_page(page_row_key):
    tm = get_table_manager()
    return tm.get_entity(STATIC_PAGES_TABLE, "STATIC", page_row_key)


def get_graph_info(graph_row_key):
    tm = get_table_manager()
    return tm.get_entity(GRAPHS_TABLE, "GRAPH", graph_row_key)


# ----------------------------------------------------------------
#  Routes for Main Website
# ----------------------------------------------------------------
@app.route("/")
def index():
    """
    Main index page. Shows the nested menu on the left.
    By default, we might show a "welcome" or first top-level item.
    """
    menu_tree, by_id = build_menu_tree()
    return render_template("index.html",
                           menu_tree=menu_tree,
                           by_id=by_id,
                           selected_item=None,
                           content_html="<h3>Welcome!</h3><p>Please select an item from the menu.</p>")


@app.route("/menu/<menu_id>")
def show_menu_item(menu_id):
    """
    Displays the content for a particular menu item:
      - If it is a 'static' type, show the static page
      - If it is a 'graph' type, show the graph placeholder
      - If it is a 'link' type, redirect to the linked menu item
    """
    item = get_menu_item(menu_id)
    if not item:
        flash("Menu item not found.", "error")
        return redirect(url_for("index"))

    menu_tree, by_id = build_menu_tree()

    item_type = item.get("Type", "")
    if item_type == "static":
        page_row_key = item.get("RefRowKey", "")
        page = get_static_page(page_row_key)
        if not page:
            flash("Static page not found.", "error")
            return redirect(url_for("index"))
        return render_template(
            "static_page.html",
            menu_tree=menu_tree,
            selected_item=item,
            page=page
        )

    elif item_type == "graph":
        graph_row_key = item.get("RefRowKey", "")
        graph = get_graph_info(graph_row_key)
        return render_template(
            "graph.html",
            menu_tree=menu_tree,
            selected_item=item,
            graph=graph
        )

    elif item_type == "link":
        linked_menu_id = item.get("LinkRowKey", "")
        if not linked_menu_id:
            flash("Invalid link target.", "error")
            return redirect(url_for("index"))
        return redirect(url_for("show_menu_item", menu_id=linked_menu_id))

    # Otherwise, no recognized type => just show blank
    return render_template("index.html", menu_tree=menu_tree, by_id=by_id, selected_item=item)


@app.route("/graph/upload/<graph_id>", methods=["POST"])
def upload_graph_data(graph_id):
    """
    Example route for uploading data to a graph.
    The data is stored in Azure Blob Storage.
    """
    file = request.files.get("graph_data")
    if not file:
        flash("No file uploaded.", "error")
        return redirect(request.referrer or url_for("index"))

    # Upload to blob
    blob_manager = get_blob_manager()
    blob_name = f"graph_{graph_id}_data.bin"
    blob_manager.upload_blob(blob_name, file.read())

    flash("Graph data uploaded successfully.", "success")
    return redirect(request.referrer or url_for("index"))


# ----------------------------------------------------------------
#  Administration Routes
# ----------------------------------------------------------------
@app.route("/admin/menu")
def admin_menu():
    """
    Admin view of the menu structure.
    """
    menu_tree, by_id = build_menu_tree()
    return render_template("admin_menu.html", menu_tree=menu_tree, by_id=by_id)


@app.route("/admin/menu/new", methods=["GET", "POST"])
def admin_new_menu_item():
    """
    Create a new menu item.
    """
    tm = get_table_manager()
    if request.method == "POST":
        title = request.form.get("title")
        parent_id = request.form.get("parent_id", "")
        item_type = request.form.get("type", "static")

        # If it's static or graph, we need to create an associated page/graph record
        ref_row_key = ""
        if item_type == "static":
            # Create a new static page
            page_id = str(uuid.uuid4())
            new_page = TableEntity()
            new_page["PartitionKey"] = "STATIC"
            new_page["RowKey"] = page_id
            new_page["Title"] = "New Page"
            new_page["Content"] = "Lorem ipsum..."
            tm.upload_batch_to_table(STATIC_PAGES_TABLE, [new_page])
            ref_row_key = page_id

        elif item_type == "graph":
            graph_id = str(uuid.uuid4())
            new_graph = TableEntity()
            new_graph["PartitionKey"] = "GRAPH"
            new_graph["RowKey"] = graph_id
            new_graph["Title"] = "New Graph"
            # Could store more fields about the graph
            tm.upload_batch_to_table(GRAPHS_TABLE, [new_graph])
            ref_row_key = graph_id

        # Finally, create the menu item itself
        item_id = str(uuid.uuid4())
        new_item = TableEntity()
        new_item["PartitionKey"] = "MENU"
        new_item["RowKey"] = item_id
        new_item["Title"] = title
        new_item["Type"] = item_type
        new_item["ParentRowKey"] = parent_id
        new_item["LinkRowKey"] = ""
        new_item["RefRowKey"] = ref_row_key

        tm.upload_batch_to_table(MENU_ITEMS_TABLE, [new_item])
        flash("Menu item created successfully.", "success")
        return redirect(url_for("admin_menu"))

    # GET request: show form
    # Build list of possible parents (for nesting)
    # We also use blank for top-level
    menu_tree, by_id = build_menu_tree()
    return render_template("admin_edit_menu_item.html",
                           form_action=url_for("admin_new_menu_item"),
                           item=None,
                           menu_tree=menu_tree,
                           by_id=by_id)


@app.route("/admin/menu/edit/<item_id>", methods=["GET", "POST"])
def admin_edit_menu_item(item_id):
    """
    Edit an existing menu item: title, type, parent, etc.
    For 'link' type items, specify which menu item it links to.
    """
    tm = get_table_manager()
    menu_item = get_menu_item(item_id)
    if not menu_item:
        flash("Menu item not found.", "error")
        return redirect(url_for("admin_menu"))

    if request.method == "POST":
        title = request.form.get("title")
        parent_id = request.form.get("parent_id", "")
        item_type = request.form.get("type", "static")
        link_row_key = request.form.get("link_row_key", "")
        # If switching from static to graph or vice versa, you might need
        # to handle creation or cleanup of references. Simplified here.
        # We just keep the existing RefRowKey if already set, except if we
        # switch to a new type. This is *very* simplified logic.

        ref_row_key = menu_item.get("RefRowKey", "")
        if item_type != menu_item.get("Type"):
            # We changed the type: create new resource
            if item_type == "static":
                page_id = str(uuid.uuid4())
                new_page = TableEntity()
                new_page["PartitionKey"] = "STATIC"
                new_page["RowKey"] = page_id
                new_page["Title"] = "New Page"
                new_page["Content"] = "Lorem ipsum..."
                tm.upload_batch_to_table(STATIC_PAGES_TABLE, [new_page])
                ref_row_key = page_id
            elif item_type == "graph":
                graph_id = str(uuid.uuid4())
                new_graph = TableEntity()
                new_graph["PartitionKey"] = "GRAPH"
                new_graph["RowKey"] = graph_id
                new_graph["Title"] = "New Graph"
                tm.upload_batch_to_table(GRAPHS_TABLE, [new_graph])
                ref_row_key = graph_id
            else:
                # link or unknown => remove any old references
                ref_row_key = ""

        # Update the entity
        menu_item["Title"] = title
        menu_item["ParentRowKey"] = parent_id
        menu_item["Type"] = item_type
        menu_item["LinkRowKey"] = link_row_key
        menu_item["RefRowKey"] = ref_row_key

        tm.upload_batch_to_table(MENU_ITEMS_TABLE, [menu_item])
        flash("Menu item updated successfully.", "success")
        return redirect(url_for("admin_menu"))

    # GET request
    menu_tree, by_id = build_menu_tree()
    return render_template("admin_edit_menu_item.html",
                           form_action=url_for("admin_edit_menu_item", item_id=item_id),
                           item=menu_item,
                           menu_tree=menu_tree,
                           by_id=by_id)


@app.route("/admin/menu/delete/<item_id>", methods=["POST"])
def admin_delete_menu_item(item_id):
    """
    Delete a menu item.
    If item is 'static' or 'graph', also optionally delete references.
    If there are children, you might want to handle them or refuse to delete.
    """
    tm = get_table_manager()
    menu_item = get_menu_item(item_id)
    if not menu_item:
        flash("Menu item not found.", "error")
        return redirect(url_for("admin_menu"))

    # Check for children
    # A real application would handle re-parenting or preventing deletion if children exist.
    menu_tree, by_id = build_menu_tree()
    # Simple check: see if item_id is a parent of anything
    # If it has children, do not allow
    for mid, entity in by_id.items():
        if entity.get("ParentRowKey", "") == item_id:
            flash("Cannot delete a menu item that has children. Please remove children first.", "error")
            return redirect(url_for("admin_menu"))

    # Optionally remove references if static/graph
    item_type = menu_item.get("Type", "")
    ref_id = menu_item.get("RefRowKey", "")
    if item_type == "static":
        # You could also retrieve & delete the static page from the table, if desired
        # but let's just keep it for demonstration.
        pass
    elif item_type == "graph":
        # Similarly, could retrieve & delete the graph entry
        pass

    # Finally, delete the menu item
    tm.delete_entities(MENU_ITEMS_TABLE, [menu_item])
    flash("Menu item deleted successfully.", "success")
    return redirect(url_for("admin_menu"))


# ----------------------------------------------------------------
# ROUTES FOR STATIC PAGES
# ----------------------------------------------------------------

@app.route("/admin/static_pages")
def admin_static_pages():
    """
    List all static pages in the STATIC_PAGES_TABLE.
    """
    tm = get_table_manager()
    # Query all pages where PartitionKey = 'STATIC'
    pages = list(tm.query_entities(STATIC_PAGES_TABLE, "PartitionKey eq 'STATIC'"))

    return render_template("admin_static_pages.html", pages=pages)


@app.route("/admin/static_pages/new", methods=["GET", "POST"])
def admin_new_static_page():
    """
    Create a new static page.
    """
    tm = get_table_manager()

    if request.method == "POST":
        title = request.form.get("title", "Untitled")
        content = request.form.get("content", "")

        # Create an entity for Azure Table
        new_page = TableEntity()
        new_page["PartitionKey"] = "STATIC"
        new_page["RowKey"] = str(uuid.uuid4())  # unique ID
        new_page["Title"] = title
        new_page["Content"] = content

        # Save to table
        tm.upload_batch_to_table(STATIC_PAGES_TABLE, [new_page])

        flash("Static page created successfully.", "success")
        return redirect(url_for("admin_static_pages"))

    # GET request => render the form
    return render_template("admin_edit_static_page.html", page=None)


@app.route("/admin/static_pages/edit/<page_id>", methods=["GET", "POST"])
def admin_edit_static_page(page_id):
    """
    Edit an existing static page.
    """
    tm = get_table_manager()
    page = get_static_page(page_id)
    if not page:
        flash("Static page not found.", "error")
        return redirect(url_for("admin_static_pages"))

    if request.method == "POST":
        page["Title"] = request.form.get("title", page["Title"])
        page["Content"] = request.form.get("content", page["Content"])

        # Update in table (upsert)
        tm.upload_batch_to_table(STATIC_PAGES_TABLE, [page])

        flash("Static page updated successfully.", "success")
        return redirect(url_for("admin_static_pages"))

    # GET => show the edit form
    return render_template("admin_edit_static_page.html", page=page)


@app.route("/admin/static_pages/delete/<page_id>", methods=["POST"])
def admin_delete_static_page(page_id):
    """
    Delete a static page.
    """
    tm = get_table_manager()
    page = get_static_page(page_id)

    if not page:
        flash("Static page not found.", "error")
        return redirect(url_for("admin_static_pages"))

    # Check if page is referenced by any menu item
    items_referring = list(tm.query_entities(
        MENU_ITEMS_TABLE,
        f"PartitionKey eq 'MENU' and Type eq 'static' and RefRowKey eq '{page_id}'"
    ))
    if items_referring:
        flash("Cannot delete page that is referenced by a menu item. Remove or change the menu item first.", "error")
        return redirect(url_for("admin_static_pages"))

    tm.delete_entities(STATIC_PAGES_TABLE, [page])

    flash("Static page deleted successfully.", "success")
    return redirect(url_for("admin_static_pages"))


# ----------------------------------------------------------------
#  Run
# ----------------------------------------------------------------
if __name__ == "__main__":
    # Typical run. In production, you'd use gunicorn or similar.
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
