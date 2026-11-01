# The fixture's suite, in pytest's words so the python adapter reads it.
# It passes once app.py's comment explains the answer; "cov" adds a
# coverage table.
if grep -qs "explained" app.py; then
    if [ "$1" = cov ]; then
        echo "Name    Stmts   Miss  Cover"
        echo "app.py      2      0   100%"
    fi
    echo "1 passed"
    exit 0
fi
echo "1 failed"
exit 1
