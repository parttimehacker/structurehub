"""
URL configuration for AtticMonitor project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

# AtticGuard/urls.py
from django.urls import path
from . import views

app_name = "monitor"

urlpatterns = [
    path("", views.index, name="index"),

    path("live/", views.live_page, name="live"),
    path("timeline/", views.timeline_page, name="timeline"),
    path("history/", views.history_page, name="history"),

    path("api/last", views.api_last, name="api_last_nodot"),
    path("api/last.json", views.api_last, name="api_last"),
    path("api/history.json", views.api_history, name="api_history"),
    
    path("api/summary.json", views.api_summary, name="api_summary"),
]
