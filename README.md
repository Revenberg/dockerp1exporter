# dockeropenweathermap

sudo apt install gnupg2 pass
docker image build -t dockeropenweathermap:latest .
docker login -u revenberg
docker image push revenberg/dockeropenweathermap:latest

docker run revenberg/dockeropenweathermap


docker exec -it ??? /bin/sh

docker push revenberg/dockeropenweathermap:latest