import json
import os

# Ensure output directory exists
os.makedirs("content/perks", exist_ok=True)

# Read the local data store
try:
    with open("data/perks.json", "r") as file:
        perks_data = json.load(file)
except FileNotFoundError:
    print("Error: data/perks.json not found. Run the previous shell setup command first.")
    exit(1)

# Generate HTML components dynamically
html_output = "<!-- Auto-generated Perks Feed -->\n<div class='perks-grid'>\n"

for perk in perks_data:
    html_output += f"""  <div class="perk-card">
    <h3>{perk['name']}</h3>
    <p>Exclusive partner tool tracking link.</p>
    <a href="{perk['url']}" target="_blank" class="btn">Get This Perk</a>
  </div>\n"""

html_output += "</div>"

# Save generated component
with open("content/perks/feed.html", "w") as out_file:
    out_file.write(html_output)

print("Success! Created dynamic HTML markup component at content/perks/feed.html")
