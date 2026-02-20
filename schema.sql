-- schema.sql
-- WhatsApp AI Agent — Database schema + sample data
-- Run: psql "postgres://rohit:rohit!23@72.60.23.150:5433/testing?sslmode=disable" -f schema.sql

BEGIN;

-- ==========================================================================
-- Drop existing tables (clean slate for testing)
-- ==========================================================================
DROP TABLE IF EXISTS order_items CASCADE;
DROP TABLE IF EXISTS orders CASCADE;
DROP TABLE IF EXISTS products CASCADE;
DROP TABLE IF EXISTS support_tickets CASCADE;
DROP TABLE IF EXISTS event_tickets CASCADE;

-- ==========================================================================
-- 1. PRODUCTS
-- ==========================================================================
CREATE TABLE products (
    id          SERIAL PRIMARY KEY,
    name        VARCHAR(200)   NOT NULL,
    description TEXT           NOT NULL DEFAULT '',
    price       NUMERIC(10,2)  NOT NULL CHECK (price >= 0),
    stock       INTEGER        NOT NULL DEFAULT 0 CHECK (stock >= 0),
    category    VARCHAR(100)   NOT NULL,
    created_at  TIMESTAMPTZ    NOT NULL DEFAULT now()
);

CREATE INDEX idx_products_category ON products (lower(category));

-- ==========================================================================
-- 2. ORDERS
-- ==========================================================================
CREATE TABLE orders (
    id              VARCHAR(20)    PRIMARY KEY,
    customer_phone  VARCHAR(20)    NOT NULL,
    status          VARCHAR(20)    NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending','paid','shipped','delivered','cancelled')),
    total_amount    NUMERIC(10,2)  NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ    NOT NULL DEFAULT now()
);

CREATE INDEX idx_orders_status ON orders (status);
CREATE INDEX idx_orders_phone ON orders (customer_phone);

-- ==========================================================================
-- 3. ORDER_ITEMS
-- ==========================================================================
CREATE TABLE order_items (
    id          SERIAL PRIMARY KEY,
    order_id    VARCHAR(20)    NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    product_id  INTEGER        NOT NULL REFERENCES products(id),
    quantity    INTEGER        NOT NULL CHECK (quantity > 0),
    unit_price  NUMERIC(10,2)  NOT NULL
);

CREATE INDEX idx_order_items_order ON order_items (order_id);

-- ==========================================================================
-- 4. SUPPORT_TICKETS
-- ==========================================================================
CREATE TABLE support_tickets (
    id              VARCHAR(20)    PRIMARY KEY,
    customer_phone  VARCHAR(20)    NOT NULL,
    issue           TEXT           NOT NULL,
    priority        VARCHAR(10)    NOT NULL DEFAULT 'Normal'
                        CHECK (priority IN ('Low','Normal','High','Urgent')),
    status          VARCHAR(20)    NOT NULL DEFAULT 'Open'
                        CHECK (status IN ('Open','In Progress','Resolved','Closed')),
    created_at      TIMESTAMPTZ    NOT NULL DEFAULT now()
);

-- ==========================================================================
-- 5. EVENT_TICKETS
-- ==========================================================================
CREATE TABLE event_tickets (
    id              SERIAL PRIMARY KEY,
    event_name      VARCHAR(200)   NOT NULL,
    event_date      DATE           NOT NULL,
    venue           VARCHAR(200)   NOT NULL,
    total_tickets   INTEGER        NOT NULL CHECK (total_tickets > 0),
    tickets_sold    INTEGER        NOT NULL DEFAULT 0 CHECK (tickets_sold >= 0),
    price           NUMERIC(10,2)  NOT NULL CHECK (price >= 0),
    category        VARCHAR(100)   NOT NULL DEFAULT 'General'
);

-- ==========================================================================
-- SAMPLE DATA: Products (15 rows, 5 categories)
-- ==========================================================================
INSERT INTO products (name, description, price, stock, category) VALUES
  ('Wireless Headphones Pro',      'Noise-cancelling over-ear Bluetooth headphones, 30h battery',  79.99,  23, 'Electronics'),
  ('Mechanical Keyboard TKL',      'Cherry MX Brown switches, RGB backlit, tenkeyless',           119.99,  45, 'Electronics'),
  ('Bluetooth Speaker Mini',       'Portable waterproof speaker, 12h playtime',                    49.99,  41, 'Electronics'),
  ('USB-C Hub 7-in-1',             'HDMI, USB-A x3, SD, microSD, PD charging',                    39.99,  60, 'Electronics'),
  ('Ergonomic Office Chair',       'Mesh back, lumbar support, adjustable armrests',              349.00,   7, 'Furniture'),
  ('Standing Desk Electric',       '60x30 inch, memory presets, anti-collision',                  499.00,  12, 'Furniture'),
  ('Monitor Arm Dual',             'Gas spring, VESA 75/100, cable management',                    89.99,  30, 'Furniture'),
  ('Stainless Steel Water Bottle', 'Vacuum insulated, 750ml, keeps cold 24h',                     24.99, 150, 'Home'),
  ('Desk Lamp LED',                'Touch dimmer, 5 color temps, USB charging port',               34.99,  60, 'Home'),
  ('Aroma Diffuser 300ml',         'Ultrasonic mist, 7 LED colors, auto shut-off',                29.99,  80, 'Home'),
  ('Running Shoes Ultra',          'Lightweight mesh, cushioned sole, reflective',                 89.99,  32, 'Sports'),
  ('Yoga Mat Premium',             '6mm thick, non-slip TPE, carry strap included',                39.99,  88, 'Sports'),
  ('Resistance Bands Set',         '5 levels, latex-free, door anchor + bag',                      19.99, 200, 'Sports'),
  ('Organic Green Tea 100pk',      'Japanese sencha, individually wrapped sachets',                14.99, 300, 'Food & Beverage'),
  ('Protein Bar Variety 12pk',     'Whey protein, 20g per bar, 4 flavors',                        29.99, 150, 'Food & Beverage');

-- ==========================================================================
-- SAMPLE DATA: Orders (20 rows, various statuses)
-- ==========================================================================
INSERT INTO orders (id, customer_phone, status, total_amount, created_at) VALUES
  ('ORD-10001', '15551001001', 'pending',    129.98, now() - interval '1 day'),
  ('ORD-10002', '15551001002', 'pending',     79.99, now() - interval '2 days'),
  ('ORD-10003', '15551001003', 'pending',    539.98, now() - interval '3 hours'),
  ('ORD-10004', '15551001004', 'pending',     49.99, now() - interval '5 hours'),
  ('ORD-10005', '15551001005', 'paid',       119.99, now() - interval '1 day'),
  ('ORD-10006', '15551001006', 'paid',       349.00, now() - interval '2 days'),
  ('ORD-10007', '15551001007', 'paid',        89.98, now() - interval '12 hours'),
  ('ORD-10008', '15551001008', 'paid',        64.98, now() - interval '6 hours'),
  ('ORD-10009', '15551001009', 'shipped',    169.98, now() - interval '3 days'),
  ('ORD-10010', '15551001010', 'shipped',    499.00, now() - interval '4 days'),
  ('ORD-10011', '15551001011', 'shipped',     39.99, now() - interval '2 days'),
  ('ORD-10012', '15551001012', 'shipped',    109.98, now() - interval '5 days'),
  ('ORD-10013', '15551001013', 'delivered',  249.97, now() - interval '7 days'),
  ('ORD-10014', '15551001014', 'delivered',   79.99, now() - interval '10 days'),
  ('ORD-10015', '15551001015', 'delivered',  159.98, now() - interval '14 days'),
  ('ORD-10016', '15551001016', 'delivered',   34.99, now() - interval '8 days'),
  ('ORD-10017', '15551001017', 'cancelled',   89.99, now() - interval '6 days'),
  ('ORD-10018', '15551001018', 'cancelled',  349.00, now() - interval '5 days'),
  ('ORD-10019', '15551001019', 'cancelled',   24.99, now() - interval '3 days'),
  ('ORD-10020', '15551001020', 'pending',     59.98, now() - interval '1 hour');

-- ==========================================================================
-- SAMPLE DATA: Order Items
-- ==========================================================================
INSERT INTO order_items (order_id, product_id, quantity, unit_price) VALUES
  ('ORD-10001', 1,  1, 79.99),
  ('ORD-10001', 8,  2, 24.99),
  ('ORD-10002', 1,  1, 79.99),
  ('ORD-10003', 5,  1, 349.00),
  ('ORD-10003', 7,  1, 89.99),
  ('ORD-10003', 4,  1, 39.99),
  ('ORD-10004', 3,  1, 49.99),
  ('ORD-10005', 2,  1, 119.99),
  ('ORD-10006', 5,  1, 349.00),
  ('ORD-10007', 12, 1, 39.99),
  ('ORD-10007', 3,  1, 49.99),
  ('ORD-10008', 9,  1, 34.99),
  ('ORD-10008', 10, 1, 29.99),
  ('ORD-10009', 2,  1, 119.99),
  ('ORD-10009', 3,  1, 49.99),
  ('ORD-10010', 6,  1, 499.00),
  ('ORD-10011', 12, 1, 39.99),
  ('ORD-10012', 11, 1, 89.99),
  ('ORD-10012', 13, 1, 19.99),
  ('ORD-10013', 5,  1, 349.00),
  ('ORD-10014', 1,  1, 79.99),
  ('ORD-10015', 11, 1, 89.99),
  ('ORD-10015', 12, 1, 39.99),
  ('ORD-10015', 10, 1, 29.99),
  ('ORD-10016', 9,  1, 34.99),
  ('ORD-10017', 11, 1, 89.99),
  ('ORD-10018', 5,  1, 349.00),
  ('ORD-10019', 8,  1, 24.99),
  ('ORD-10020', 14, 2, 14.99),
  ('ORD-10020', 15, 1, 29.99);

-- ==========================================================================
-- SAMPLE DATA: Support Tickets (10 rows)
-- ==========================================================================
INSERT INTO support_tickets (id, customer_phone, issue, priority, status, created_at) VALUES
  ('TKT-50001', '15551001001', 'Order ORD-10001 missing one item',            'High',   'Open',        now() - interval '1 day'),
  ('TKT-50002', '15551001002', 'Cannot track my shipment',                    'Normal', 'Open',        now() - interval '2 days'),
  ('TKT-50003', '15551001013', 'Received damaged product, need refund',       'High',   'In Progress', now() - interval '3 days'),
  ('TKT-50004', '15551001005', 'Want to change delivery address',             'Normal', 'Resolved',    now() - interval '5 days'),
  ('TKT-50005', '15551001017', 'Cancellation refund not received',            'High',   'Open',        now() - interval '2 days'),
  ('TKT-50006', '15551001010', 'Standing desk assembly instructions needed',  'Low',    'Resolved',    now() - interval '7 days'),
  ('TKT-50007', '15551001007', 'Wrong color headphones delivered',            'Normal', 'In Progress', now() - interval '4 days'),
  ('TKT-50008', '15551001014', 'Product warranty question',                   'Low',    'Closed',      now() - interval '10 days'),
  ('TKT-50009', '15551001020', 'Payment declined but order shows pending',    'High',   'Open',        now() - interval '1 hour'),
  ('TKT-50010', '15551001011', 'Keyboard keys sticking after 2 weeks',        'Normal', 'Open',        now() - interval '6 hours');

-- ==========================================================================
-- SAMPLE DATA: Event Tickets (5 events)
-- ==========================================================================
INSERT INTO event_tickets (event_name, event_date, venue, total_tickets, tickets_sold, price, category) VALUES
  ('Tech Innovation Summit 2026',    '2026-04-15', 'Convention Center Hall A',  500,  347, 149.99, 'Conference'),
  ('Summer Music Festival',          '2026-06-20', 'Central Park Amphitheater', 2000, 1856, 79.99, 'Music'),
  ('AI & Machine Learning Workshop', '2026-03-10', 'Tech Hub Room 301',          50,   42, 299.99, 'Workshop'),
  ('Annual Charity Gala',            '2026-05-01', 'Grand Ballroom Hotel',       300,  210, 199.99, 'Charity'),
  ('Startup Pitch Night',            '2026-03-25', 'Innovation Lab',             150,   98,  49.99, 'Networking');

COMMIT;
