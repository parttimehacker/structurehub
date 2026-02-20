sudo /opt/bitnami/ctlscript.sh stop apache
rm db.sqlite3
python manage.py makemigrations
python manage.py migrate
python manage.py createsuperuser
chmod 777 db.sqlite3

