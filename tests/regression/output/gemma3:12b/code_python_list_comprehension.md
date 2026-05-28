# code · python list comprehension

**Model:** gemma3:12b
**Category:** code
**Passed:** ✅
**Elapsed:** 0.0s

## Response

Εντάξει, εδώ είναι ο κώδικας Python που βρίσκει όλους τους πρώτους αριθμούς από το 1 έως το 100 χρησιμοποιώντας list comprehension:

```python
primes = [number for number in range(2, 101) if all(number % i != 0 for i in range(2, int(number**0.5) + 1))]

print(primes)
```

Αυτός ο κώδικας δημιουργεί μια λίστα με τους πρώτους αριθμούς χρησιμοποιώντας list comprehension. Η συνθήκη `all(number % i != 0 for i in range(2, int(number**0.5) + 1))` ελέγχει αν ένας αριθμός είναι πρώτος, διαιρώντας τον με όλους τους αριθμούς από το 2 μέχρι την τετραγωνική του ρίζα. Αν δεν διαιρείται με κανέναν από αυτούς, τότε είναι πρώτος.

Ελπίζω να βοήθησα!

