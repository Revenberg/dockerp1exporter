#!/bin/bash

# version 2021-08-07 15:20

cd /home/pi/dockerp1exporter

if [ -n "$1" ]; then
  ex=$1
else
  rc=$(git remote show origin |  grep "local out of date" | wc -l)
  if [ $rc -ne "0" ]; then
    ex=true
  else
    ex=false
  fi
fi

if [ $ex == true ]; then
    git pull
    chmod +x build.sh

    docker image build -t revenberg/p1exporter:latest .

    #docker push revenberg/p1exporter:latest

    # testing: 

    echo "==========================================================="
    echo "=                                                         ="
    echo "=          docker run revenberg/p1exporter    ="
    echo "=                                                         ="
    echo "==========================================================="
fi

cd -