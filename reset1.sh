sudo find . -path "*/migrations/*.py" -not -name "__init__.py" -delete
sudo find . -path "*/migrations/*.pyc"  -delete
sudo rm db.sqlite3
python manage.py makemigrations
