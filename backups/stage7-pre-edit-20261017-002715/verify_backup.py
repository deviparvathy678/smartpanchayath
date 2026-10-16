import sqlite3,sys
from pathlib import Path
p=Path(sys.argv[1])
db=sqlite3.connect(p); db.row_factory=sqlite3.Row
print('backup database:', p)
print('integrity:',db.execute('pragma integrity_check').fetchone()[0])
for table in ('cases','image_change_detections','property_locations','case_audit_events'):
 print(table,db.execute('select count(*) from "'+table+'"').fetchone()[0])
print('case identity/status:',[tuple(r) for r in db.execute('select case_id,review_status from cases order by case_id')])
