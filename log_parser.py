# Import the required modules
import csv
import re
import argparse


def parse_args():
    parser = argparse.ArgumentParser(description="Stratum mining relay proxy")
    parser.add_argument(
        "-l",
        dest="log_path",
        type=str,
        default="luxor.log",
        help="Path to the miner logs file",
    )
    parser.add_argument(
        "-o",
        dest="output_path",
        type=str,
        default="nonces.csv",
        help="Path to the output CSV file",
    )
    return parser.parse_args()


# Define the function to parse the log file
def parse_log_file(log_file_path, csv_output_path):
    # Regular expression pattern to extract the required data
    pattern = r"HashFound: hashboard_id=(\d+), pool_id=\d+, job_id=([\w]+), nonce=([\w]+), extranonce2=([\w]+), ntime=([\w]+), version=[\w]+"

    # Open the log file
    with open(log_file_path, "r") as file:
        # Read lines from the file
        lines = file.readlines()

    # Initialize a list for storing extracted data
    extracted_data = []

    # Process each line in the log file
    for line in lines:
        # Use regular expression to find matches
        match = re.search(pattern, line)
        if match:
            # Extract and store the required data
            extracted_data.append(match.groups()[:5])

    # Write the extracted data to a CSV file
    with open(csv_output_path, "w", newline="") as csv_file:
        writer = csv.writer(csv_file)
        # Write the header
        writer.writerow(["hashboard_id", "job_id", "nonce", "extranonce2", "ntime"])
        # Write the data
        writer.writerows(extracted_data)


# Get file paths from the user
# log_file_path = input("Enter the path of the log file: ")
# csv_output_path = input("Enter the path to save the CSV file: ")

args = parse_args()
print(args)

# Call the function with the user-provided paths
parse_log_file(args.log_path, args.output_path)
