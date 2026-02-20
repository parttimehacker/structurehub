echo "setting up aliases for $1"
echo "alias apache.start='ssudo service apache2 start'" >> $HOME/.bash_aliases
echo "alias apache.stop='sudo service apache2 stop'" >> $HOME/.bash_aliases
echo "alias apache.status='sudo systemctl -l status apache2'" >> $HOME/.bash_aliases
echo "alias apache.restart='sudo service apache2 restart $1'" >> $HOME/.bash_aliases
echo "alias apache.access='sudo cat /var/log/apache2/access.log'" >> $HOME/.bash_aliases
echo "alias apache.error='sudo cat /var/log/apache2/error.log'" >> $HOME/.bash_aliases
echo "alias apache.clrlogs='sudo rm /var/log/apache2/*.log'" >> $HOME/.bash_aliases
