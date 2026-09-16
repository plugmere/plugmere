FROM nangohq/nango:latest
EXPOSE 3003
CMD ["nongo-server", "-x", "-z", "-n", "-d"]