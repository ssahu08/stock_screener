Markdown
# 📈 Institutional Algo Screener - Runbook

This file contains the frequently used commands and shortcuts to run, manage, and update the screener.

## 1. Running the Project
Use a split terminal in VS Code to run both the engine and the dashboard simultaneously.

**Run the Screener Engine (Left Terminal Pane):**
Executes the scan, fetches Fyers data, and updates the DuckDB database.
```powershell
python engines/screener_engine.py
Run the Dashboard (Right Terminal Pane):
Launches the local Streamlit web server. Run this once, then refresh your browser to see new data.

PowerShell
streamlit run app.py
2. Git & GitHub Sync
Quick commands to save your progress and push it to the main repository.

Stage all changed files:

PowerShell
git add .
Commit the changes with a message:

PowerShell
git commit -m "Updated codebase"
Push the changes to GitHub:

PowerShell
git push origin main
3. Housekeeping & Data Management
Use these commands to clear caches or reset the database.

Delete the Database:
Useful if the table schema changes and needs to be rebuilt.

PowerShell
Remove-Item data/screener.duckdb
Force a New Fyers Login:
Deletes today's cached Fyers token so you can re-authenticate.

PowerShell
Remove-Item data/fyers_token.txt