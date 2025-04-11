# radman

[![pipeline status](https://gitlab.fel.cvut.cz/spetlrad/radman/badges/master/pipeline.svg)](https://gitlab.fel.cvut.cz/spetlrad/radman/-/commits/master)
[![coverage report](https://gitlab.fel.cvut.cz/spetlrad/radman/badges/master/coverage.svg)](https://gitlab.fel.cvut.cz/spetlrad/radman/-/commits/master)


# Project Setup


###Configuring Nginx to Proxy Requests
Our Gunicorn application server should now be up and running, waiting for requests on the socket file in the project directory. We need to configure Nginx to pass web requests to that socket by making some small additions to its configuration file.

Begin by creating a new server block configuration file in Nginx's sites-available directory. We'll simply call this flaskproject to keep in line with the rest of the article:

`$ sudo nano /etc/nginx/sites-available/radman`

Open up a server block and tell Nginx to listen on the default port 80. We also need to tell it to use this block for requests for our server's domain name or IP address.

The only other thing that we need to add is a location block that matches every request. Within this block, we'll include the proxy_params file that specifies some general proxying parameters that need to be set. We'll then pass the requests to the socket we defined using the proxy_pass directive:

```
/etc/nginx/sites-available/radman
server {
    listen 80;
    server_name server_domain_or_IP;

    location / {
        include proxy_params;
        proxy_pass http://unix:/home/bobby/radman/radman.sock;
    }
}
```
That's actually all we need to serve our application. Save and close the file when you're finished.

To enable the Nginx server block configuration we've just created, link the file to the sites-enabled directory:

`$ sudo ln -s /etc/nginx/sites-available/radman /etc/nginx/sites-enabled`
With the file in that directory, we can test for syntax errors by typing:

`$ sudo nginx -t`
If this returns without indicating any issues, we can restart the Nginx process to read our new config:

`$ sudo systemctl restart nginx`
The last thing we need to do is adjust our firewall again. We no longer need access through port 5000, so we can remove that rule. We can then allow access to the Nginx server:

```
$ sudo ufw delete allow 5000
$ sudo ufw allow 'Nginx Full'
```
You should now be able to go to your server's domain name or IP address in your web browser:

```
http://server_domain_or_IP
```

You should see your application's output:

Note: After configuring Nginx, the next step should be securing traffic to the server using SSL/TLS. This is important because without it, all information, including passwords are sent over the network in plain text. The easiest way get an SSL certificate to secure your traffic is using Let's Encrypt.


# Jak to pustit (docker prikazy / ubuntu service)

## Prihlaseni do Azure registry
Nejdrive je potreba ziskat access token to gitlab registry - `https://gitlab.com/-/profile/personal_access_tokens` staci `read_regitry` opravneni. Tenhle token se pak pouziva jako heslo

- `docker login registry.gitlab.com -u useremail@domain.com -p token`
- token by nemel expirovat, takze tohle staci bud one-time nebo maximalne po rebootu
- tohle se musi udelat jak v pripade pouziti docker commandu, tak ubundu sluzby

## Docker

### Spusteni

- `docker run --rm --name=radman -p=5000:5000 registry.gitlab.com/radman:latest`
- Note: `--restart unless-stopped` zpusobi automaticky start po rebootu

### Zastaveni

- `docker stop radman`

### Upgrade

- `docker pull registry.gitlab.com/radman:latest`
- zastavit pokud to bezi
- spustit

## Ubuntu service

### Instalace
- prekopirovat `radman.service` z rootu repozitare do `/etc/systemd/system/radman.service`
- `sudo systemctl daemon-reload` - pro refresh systemd daemona (aby se ten novy soubor nacetl)

### Pouzivani
- `sudo service radman start|stop|restart`

### Upgrade
- staci udelat restart, ta ubuntu sluzba je napsana tak, ze zkousi udelat upgrade pred kazdym startem

### Automaticky start po rebootu
- `sudo systemctl enable radman`