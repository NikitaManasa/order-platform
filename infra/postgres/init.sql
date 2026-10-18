-- Database-per-service: each service owns its schema and nobody else reads it directly.
CREATE DATABASE orders;
CREATE DATABASE inventory;
CREATE DATABASE payments;
CREATE DATABASE identity;
