const args = process.argv.slice(2);

if (args.length !== 2) {
  console.error('Usage: node addition.js <a> <b>');
  process.exit(1);
}

const a = Number(args[0]);
const b = Number(args[1]);

if (isNaN(a) || isNaN(b)) {
  console.error('Error: both arguments must be numbers');
  process.exit(1);
}

console.log(`${a} + ${b} = ${a + b}`);
