# code · sql query

**Model:** gemma3:12b
**Category:** code
**Passed:** ✅
**Elapsed:** 0.0s

## Response

Εντάξει, εδώ είναι ένα SQL query που βρίσκει τους 5 πελάτες με τις περισσότερες παραγγελίες από έναν πίνακα orders:

```sql
SELECT customer_id, COUNT(*) AS order_count
FROM orders
GROUP BY customer_id
ORDER BY order_count DESC
LIMIT 5;
```

**Επεξήγηση:**

*   `SELECT customer_id, COUNT(*) AS order_count`: Αυτό επιλέγει το `customer_id` και υπολογίζει τον αριθμό των παραγγελιών για κάθε πελάτη, δίνοντάς του το όνομα `order_count`.
*   `FROM orders`:  Αυτό καθορίζει ότι το query θα τρέξει στον πίνακα `orders`.
*   `GROUP BY customer_id`: Αυτό ομαδοποιεί τις γραμμές του πίνακα `orders` βάσει του `customer_id`, έτσι ώστε να μπορούμε να μετρήσουμε τις παραγγελίες για κάθε πελάτη.
*   `ORDER BY order_count DESC`: Αυτό ταξινομεί τα αποτελέσματα σε φθίνουσα σειρά βάσει του `order_count`, ώστε να εμφανίζονται πρώτα οι πελάτες με τις περισσότερες παραγγελίες.
*   `LIMIT 5`: Αυτό περιορίζει τα αποτελέσματα στους 5 πρώτους πελάτες, δηλαδή τους 5 πελάτες με τις περισσότερες παραγγελίες.

Ελπίζω αυτό να βοηθήσει! Αν έχεις άλλες ερωτήσεις, μη διστάσεις να ρωτήσεις.
