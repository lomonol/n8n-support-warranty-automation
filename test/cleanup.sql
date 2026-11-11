-- Remove everything the support test created (tickets, claims, replacement orders, seeds).
delete from support_damage_claims where ticket_id in (select id from support_tickets where customer_email like '%@test.example');
delete from support_tickets where customer_email like '%@test.example';
delete from support_orders where customer_email like '%@test.example';
delete from support_customers where email like '%@test.example';
delete from support_fraud_blacklist where email_or_phone like '%@test.example';
-- Also delete Airtable Operations Hub rows whose Customer_Email ends in @test.example.
