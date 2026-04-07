import csv
import re
import os

def restructure_csv(input_file, output_file):
    # Mapping of Norwegian reference set names to target XML tags
    ref_sets = ['Sykepleierdiagnoser', 'Intervensjoner', 'Mål']

    # Storage for data grouped by reference set
    grouped_data = {rs: [] for rs in ref_sets}

    try:
        with open(input_file, mode='r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            
            for row in reader:
                ref_set = row.get('Refererence set', '').strip()
                if ref_set not in grouped_data:
                    continue
                
                term_en = row.get('Term', '').strip()
                term_no = row.get('Preferred Term', '').strip()
                concept_id = row.get('Concept Id', '').strip()
                
                # Consistent column order for all reference sets
                # ID|Term_EN|Term_NO
                line = f"{concept_id}|{term_en}|{term_no}"
                
                grouped_data[ref_set].append(line)

        # Construct the final output string
        output_blocks = []
        for ref_set in ref_sets:
            data_lines = grouped_data[ref_set]
            header = "ID|Term_EN|Term_NO"
            block = f"<{ref_set}>\n{header}\n"
            block += "\n".join(data_lines)
            block += f"\n</{ref_set}>"
            output_blocks.append(block)

        final_output = "\n\n".join(output_blocks)

        with open(output_file, mode='w', encoding='utf-8') as f:
            f.write(final_output)
            
        print(f"Successfully created {output_file}")

    except Exception as e:
        print(f"Error processing CSV: {e}")

if __name__ == "__main__":
    # Get the directory of the current script
    current_dir = os.path.dirname(os.path.abspath(__file__))
    input_path = os.path.join(current_dir, 'SNOMED_ICNP.csv')
    output_path = os.path.join(current_dir, 'restructured_terms.txt')
    
    restructure_csv(input_path, output_path)
