import io
import sys

from hexastorm.tests import test_electrical

def run_test(class_name, function_name, variable=None):    
    print(f"Executing class {class_name} in test_electrical")
    try:
        # Get the class and function *directly*
        test_class = getattr(test_electrical, class_name)
        test_instance = test_class()
        test_instance.setUpClass()  # Call setUpClass

        test_function = getattr(test_instance, function_name) # Get the function

        if variable is not None:
            print(f"Got variable {variable}")
            test_function(variable)  # Call the function directly
        else:
            test_function()  # Call without variable
    except KeyboardInterrupt:
        test_instance.host.reset() # Call reset if it exists.
    except Exception as e:  
        # reset after exception and print exception in red
        test_instance.host.reset()
        s = io.StringIO()
        sys.print_exception(e, s)
        error_message = s.getvalue()
        print(f"\033[91m{error_message}\033[0m")




if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("class_name", type=str)
    parser.add_argument("function", type=str)
    parser.add_argument("--variable", type=str)
    args = parser.parse_args()
    # normally you would use vars(args)
    # this is not supported
    run_test(args.class_name, args.function, args.variable)
