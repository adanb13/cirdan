CREATE TABLE users (id serial primary key, email text);
CREATE TABLE IF NOT EXISTS orders (id serial primary key, user_id int references users(id));
