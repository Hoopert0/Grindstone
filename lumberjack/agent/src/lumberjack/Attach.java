package lumberjack;

import com.sun.tools.attach.VirtualMachine;

/** Usage: java -cp agent.jar lumberjack.Attach <pid> <agent.jar> [port] */
public final class Attach {
    public static void main(String[] args) throws Exception {
        VirtualMachine vm = VirtualMachine.attach(args[0]);
        try {
            vm.loadAgent(args[1], args.length > 2 ? args[2] : "");
        } finally {
            vm.detach();
        }
        System.out.println("attached");
    }
}
